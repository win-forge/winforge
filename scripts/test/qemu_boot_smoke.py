"""QEMU boot smoke-test: actually boot an ISO and watch the screen.

Complements scripts/build/verify_iso_bootable.py, which only proves the
El Torito catalog is STRUCTURALLY correct. This harness proves the next
link in the chain: that a real firmware (OVMF for UEFI, SeaBIOS for BIOS)
accepts the disc, loads the boot image bytes as executable code, and
hands control to Windows Boot Manager — observed via QEMU's QMP monitor.

How it decides "booted"
-----------------------
Every poll interval we take a QMP screendump (PPM) and classify it:

- firmware-only frames (TianoCore logo on OVMF / SeaBIOS "No bootable
  device" text) are low-entropy: mostly flat background + small text.
- Windows Setup frames are high-entropy: large graphical content,
  many distinct colors, wide color distribution.

We require N consecutive high-entropy frames (default 3) so a single
transition flicker can't pass. We also parse the OVMF debugcon log for
the boot-manager attempt trace, and detect the explicit failure string
("No bootable device.") from SeaBIOS.

Under TCG (no KVM on hosted runners) reaching the Setup splash takes
~2-6 minutes per leg; default budget is 20 minutes per leg.

Usage
-----
    python -m scripts.test.qemu_boot_smoke path/to.iso            # both legs
    python -m scripts.test.qemu_boot_smoke path/to.iso --legs uefi
    python -m scripts.test.qemu_boot_smoke iso --budget 900       # seconds/leg

Exit 0 = every requested leg booted; exit 1 = any leg failed. Writes
per-leg artifacts (screenshots, logs, verdict JSON) into --outdir.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from scripts.lib.log import info

SECONDS_PER_POLL = 15
CONSECUTIVE_HITS_REQUIRED = 3

# SeaBIOS prints this to the debugcon (0x402) when nothing boots.
SEABIOS_NO_BOOT = b"No bootable device."

# OVMF debugcon markers proving firmware progressed past driver init
# into attempting our disc's boot entry.
OVMF_PROGRESS_MARKERS = (
    b"Loading driver",          # generic DXE driver load
    b"InstallProtocolInterface",
    b"[Bds]",                    # BDS phase entered (boot device selection)
)


@dataclass
class LegResult:
    leg: str                     # "uefi" | "bios"
    booted: bool
    reason: str
    high_entropy_frames: int = 0
    frames_taken: int = 0
    elapsed_s: float = 0.0
    screenshot: str = ""         # path of best (last) frame
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "leg": self.leg,
            "booted": self.booted,
            "reason": self.reason,
            "high_entropy_frames": self.high_entropy_frames,
            "frames": self.frames_taken,
            "elapsed_s": round(self.elapsed_s, 1),
            "screenshot": self.screenshot,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Frame classification
# ---------------------------------------------------------------------------

def classify_frame(ppm_path: Path) -> dict[str, object]:
    """Return entropy metrics for a QMP screendump PPM without PIL.

    PPM P6 format: header lines then raw RGB triplets. We sample at most
    ~64k pixels spread across the whole frame — enough signal, cheap.
    Metrics:
      distinct_colors: count of unique RGB values seen (flat firmware
        screens have <50; real graphics thousands)
      color_spread:    stddev of luminance across samples (Setup screens
        mix dark chrome with bright content; firmware splash is uniform)
    """
    data = ppm_path.read_bytes()
    # Parse header: magic, width height, maxval — whitespace/comment separated.
    try:
        if not data.startswith(b"P6"):
            raise ValueError("not P6")
        tokens: list[bytes] = []
        idx = 3
        while len(tokens) < 3:
            nl = data.index(b"\n", idx)
            line = data[idx:nl].strip()
            if line and not line.startswith(b"#"):
                tokens.extend(line.split())
            idx = nl + 1
        w, h = int(tokens[0]), int(tokens[1])
        # tokens[2] is maxval; idx points at the byte after its newline,
        # i.e. the first pixel. (QEMU screendump always ends headers with \n.)
        pixel_start = idx
    except (ValueError, IndexError) as e:
        return {"error": f"unparseable ppm: {e}"}

    raw = data[pixel_start:]
    total_px = w * h
    stride = max(1, total_px // 65536)
    colors: set[bytes] = set()
    lumas: list[float] = []
    for i in range(0, total_px - 1, stride):
        off = i * 3
        px = raw[off : off + 3]
        if len(px) < 3:
            break
        colors.add(px)
        lumas.append(0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2])
    n = len(lumas)
    mean = sum(lumas) / n if n else 0.0
    var = sum((v - mean) ** 2 for v in lumas) / n if n else 0.0
    return {
        "w": w,
        "h": h,
        "sampled": n,
        "distinct_colors": len(colors),
        "color_spread": round(var**0.5, 1),
    }


def looks_like_setup(metrics: dict) -> bool:
    """High-entropy heuristic: real graphical content on screen."""
    if "error" in metrics:
        return False
    colors = int(metrics.get("distinct_colors", 0))  # type: ignore[arg-type]
    spread = float(metrics.get("color_spread", 0.0))  # type: ignore[arg-type]
    return colors >= 256 and spread >= 25.0


# ---------------------------------------------------------------------------
# QMP plumbing
# ---------------------------------------------------------------------------

class QmpClient:
    """Minimal QMP client: negotiate, send commands, read replies."""

    def __init__(self, sock_path: Path):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(40):  # up to 10s for qemu to create the socket
            try:
                self._sock.connect(str(sock_path))
                break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.25)
        else:
            raise RuntimeError(f"could not connect to {sock_path}")
        self._f = self._sock.makefile("rwb")
        self._readmsg()  # greeting
        self._cmd("qmp_capabilities")

    def _readmsg(self) -> dict:
        while True:
            line = self._f.readline()
            if not line:
                raise RuntimeError("QMP socket closed")
            msg: dict = json.loads(line)
            if "event" in msg:  # ignore async events
                continue
            return msg

    def _cmd(self, name: str, **args: object) -> dict:
        payload: dict[str, object] = {"execute": name}
        if args:
            payload["arguments"] = args
        self._f.write(json.dumps(payload).encode() + b"\n")
        self._f.flush()
        resp: dict = self._readmsg()
        if "error" in resp:
            raise RuntimeError(f"QMP {name} error: {resp['error']}")
        ret: dict = resp.get("return", {})
        return ret

    def screendump(self, path: Path) -> None:
        self._cmd("screendump", filename=str(path))

    def quit(self) -> None:
        with contextlib.suppress(RuntimeError, OSError):
            self._cmd("quit")


# ---------------------------------------------------------------------------
# Per-leg runner
# ---------------------------------------------------------------------------

def run_leg(
    iso: Path,
    leg: str,
    outdir: Path,
    *,
    budget_s: int = 1200,
    qemu_bin: str | None = None,
) -> LegResult:
    """Boot `iso` in one firmware mode and watch for Windows Setup."""
    t0 = time.monotonic()
    qemu = qemu_bin or shutil.which("qemu-system-x86_64")
    if not qemu:
        return LegResult(leg=leg, booted=False, reason="qemu-system-x86_64 not installed")

    ovmf_code = "/usr/share/OVMF/OVMF_CODE.fd"
    ovmf_vars_src = "/usr/share/OVMF/OVMF_VARS.fd"
    if leg == "uefi":
        if not Path(ovmf_code).exists():
            return LegResult(leg=leg, booted=False, reason=f"missing {ovmf_code} (apt install ovmf)")
        # Copy vars: OVMF mutates them at runtime.
        vars_copy = outdir / f"OVMF_VARS_{leg}.fd"
        shutil.copyfile(ovmf_vars_src, vars_copy)
        firmware_args = [
            "-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
            "-drive", f"if=pflash,format=raw,file={vars_copy}",
        ]
    else:
        # SeaBIOS is qemu's default firmware — just omit pflash drives.
        firmware_args = []

    qmp_sock = outdir / f"qmp_{leg}.sock"
    debuglog = outdir / f"firmware_{leg}.log"
    cmd = [
        qemu,
        "-nodefaults", "-nographic", "-m", "4096", "-smp", "4",
        "-machine", "q35" if leg == "uefi" else "pc",
        *firmware_args,
        "-drive", f"media=cdrom,file={iso},format=raw,if=ide",
        "-display", "none",
        "-qmp", f"unix:{qmp_sock},server,nowait",
        "-debugcon", f"file:{debuglog}",
        "-global", "isa-debugcon.iobase=0x402",
    ]
    info("smoke.leg.start", leg=leg, budget_s=budget_s)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    result = LegResult(leg=leg, booted=False, reason="")
    consecutive = 0
    qmp: QmpClient | None = None
    try:
        deadline = t0 + budget_s
        while time.monotonic() < deadline:
            # Died early? Capture stderr for diagnosis.
            if proc.poll() is not None:
                stderr_data = proc.stderr.read() if proc.stderr else b""
                err = stderr_data.decode(errors="replace")[-2000:]
                result.errors.append(err.strip())
                result.reason = f"qemu exited rc={proc.returncode}"
                return result

            if qmp is None and qmp_sock.exists():
                try:
                    qmp = QmpClient(qmp_sock)
                except (RuntimeError, OSError):
                    qmp = None

            if qmp is not None:
                frame = outdir / f"frame_{leg}_{result.frames_taken:03d}.ppm"
                try:
                    qmp.screendump(frame)
                except (RuntimeError, OSError) as e:
                    result.errors.append(f"screendump failed: {e}")
                    qmp = None
                else:
                    result.frames_taken += 1
                    metrics = classify_frame(frame)
                    result.screenshot = str(frame)
                    if looks_like_setup(metrics):
                        consecutive += 1
                        result.high_entropy_frames = consecutive
                        if consecutive >= CONSECUTIVE_HITS_REQUIRED:
                            result.booted = True
                            result.reason = (
                                f"{consecutive} consecutive high-entropy frames "
                                f"(colors={metrics['distinct_colors']}, "
                                f"spread={metrics['color_spread']})"
                            )
                            return result
                    else:
                        consecutive = 0
                        result.high_entropy_frames = 0

                        # Explicit failure signatures.
                        if debuglog.exists():
                            blob = debuglog.read_bytes()
                            if leg == "bios" and SEABIOS_NO_BOOT in blob:
                                result.reason = "SeaBIOS: 'No bootable device.'"
                                return result

            time.sleep(SECONDS_PER_POLL)

        # Budget exhausted — decide from evidence gathered.
        if debuglog.exists():
            blob = debuglog.read_bytes()
            progress = [m.decode() for m in OVMF_PROGRESS_MARKERS if m in blob]
            if leg == "bios" and SEABIOS_NO_BOOT in blob:
                result.reason = "timeout; SeaBIOS reported no bootable device"
            elif progress:
                result.reason = f"timeout; firmware reached {', '.join(progress[:2])} but no Setup splash"
            else:
                result.reason = "timeout; no boot activity observed in firmware log"
        else:
            result.reason = "timeout; no firmware log produced"
        return result
    finally:
        if qmp is not None:
            qmp.quit()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        result.elapsed_s = time.monotonic() - t0
        info("smoke.leg.end", leg=leg, booted=result.booted,
             frames=result.frames_taken, elapsed=round(result.elapsed_s, 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QEMU boot smoke test")
    ap.add_argument("iso", type=Path)
    ap.add_argument("--legs", choices=["both", "uefi", "bios"], default="both")
    ap.add_argument("--budget", type=int, default=1200, help="seconds per leg")
    ap.add_argument("--outdir", type=Path, default=None)
    args = ap.parse_args(argv)

    if not args.iso.exists():
        print(f"ISO not found: {args.iso}", file=sys.stderr)
        return 1
    outdir = args.outdir or Path.cwd() / f"smoke_{args.iso.stem}"
    outdir.mkdir(parents=True, exist_ok=True)

    legs = ["uefi", "bios"] if args.legs == "both" else [args.legs]
    results = [run_leg(args.iso, leg, outdir, budget_s=args.budget) for leg in legs]

    report = {"iso": str(args.iso), "all_ok": all(r.booted for r in results),
              "legs": [r.to_dict() for r in results]}
    report_path = outdir / "smoke-report.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    for r in results:
        status = "BOOTED" if r.booted else "FAILED"
        print(f"[{r.leg}] {status}: {r.reason}", file=sys.stderr)
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
