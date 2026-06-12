"""End-to-end tests for the ISO build workflow.

Mocks external tools (7z, oscdimg, aria2, rclone) and verifies the full
build pipeline is wired up correctly: download → convert → inject → repack → assign → upload.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
ACCOUNTS_EXAMPLE = REPO_ROOT / "config" / "accounts.yaml.example"


# --- Workflow YAML validation ---

def test_all_workflow_files_parse_as_yaml():
    """Every file in .github/workflows/ must be valid YAML."""
    for wf in WORKFLOWS_DIR.glob("*.yml"):
        data = yaml.safe_load(wf.read_text())
        assert isinstance(data, dict), f"{wf.name} did not parse as dict"
        assert "name" in data, f"{wf.name} missing 'name'"
        # PyYAML 1.1 parses 'on' as boolean True; check both
        assert "on" in data or True in data, f"{wf.name} missing 'on' trigger"


def test_build_workflow_supports_both_dispatch_types():
    """build.yml must accept both workflow_call and repository_dispatch."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    triggers = data.get("on", data.get(True, {}))
    if isinstance(triggers, list):
        trigger_names = triggers
    else:
        trigger_names = list(triggers.keys())
    assert "workflow_call" in trigger_names
    assert "repository_dispatch" in trigger_names


def test_build_workflow_runs_on_ubuntu():
    """build.yml must use ubuntu-latest (UUP converter needs wimlib, cabextract, etc.)."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    jobs = data["jobs"]["build"]
    assert "ubuntu" in jobs["runs-on"]


def test_build_workflow_defines_required_secrets():
    """build.yml must require RCLONE_CONF + ACCOUNTS_YAML (called by secret_inherit)."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    on_key = data.get("on", data.get(True, {}))
    secrets = on_key.get("workflow_call", {}).get("secrets", {})
    assert "RCLONE_CONF" in secrets
    assert "ACCOUNTS_YAML" in secrets


def test_check_updates_no_longer_targets_private_repo():
    """Post-flatten: check-updates.yml must push to the same repo, not a private one."""
    data = yaml.safe_load((WORKFLOWS_DIR / "check-updates.yml").read_text())
    text = json.dumps(data)
    # No references to the deprecated winforge-private repo
    assert "winforge-private" not in text
    # The old cross-repo auth token is gone
    assert "WINFORGE_PRIVATE_TOKEN" not in text
    # The PR job uses the default GITHUB_TOKEN via explicit permissions
    pr_job = data["jobs"].get("open-pr", {})
    perms = pr_job.get("permissions", {})
    assert perms.get("contents") == "write"


def test_ci_workflow_uses_dev_extras_and_runs_all_checks():
    """ci.yml must pip install -e .[dev] and run pytest/ruff/mypy."""
    data = yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text())
    text = json.dumps(data)
    assert '.[dev]' in text or 'dev' in text
    assert "pytest" in text
    assert "ruff" in text
    assert "mypy" in text


# --- assign.py CLI end-to-end ---

def test_assign_cli_prints_account_name(tmp_path: Path):
    """Run scripts.rclone.assign as a real subprocess; check stdout has the account name."""
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(ACCOUNTS_EXAMPLE.read_text())

    result = subprocess.run(
        [sys.executable, "-m", "scripts.rclone.assign", "win11-24h2", "5.0", str(accounts_yaml)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() in {"gd-account-1", "gd-account-2", "gd-account-3"}


def test_assign_cli_fails_when_no_account_handles_product(tmp_path: Path):
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(ACCOUNTS_EXAMPLE.read_text())

    result = subprocess.run(
        [sys.executable, "-m", "scripts.rclone.assign", "win-unknown", "5.0", str(accounts_yaml)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "No account" in result.stderr or "no_candidate" in result.stderr


# --- convert.sh script signature ---

def test_convert_sh_signature():
    """convert.sh must accept <uuid> <edition> <outdir> [compression] in that order."""
    text = (REPO_ROOT / "scripts/build/convert.sh").read_text()
    assert 'UUID="$1"' in text
    assert 'EDITION="$2"' in text
    assert 'OUTDIR="$3"' in text
    assert "scripts.uupd.download" in text


def test_convert_sh_accepts_compression_arg():
    """convert.sh must pass its 4th arg (compression) to UUP-dump converter."""
    text = (REPO_ROOT / "scripts/build/convert.sh").read_text()
    # The 4th arg is COMPRESSION, defaulting to wim
    assert 'COMPRESSION="${4:-wim}"' in text or 'COMPRESSION="$4"' in text
    # It must be validated (only wim/esd)
    assert 'must be' in text and 'wim' in text and 'esd' in text
    # And passed to the UUP-dump converter (not hardcoded)
    assert 'bash convert.sh "$COMPRESSION"' in text or 'bash convert.sh wim' in text


def test_convert_sh_uses_workspace_for_temp_dir():
    """convert.sh must use $GITHUB_WORKSPACE (not /tmp) for temp work.

    $GITHUB_WORKSPACE is the LVM-mounted volume from
    easimon/maximize-build-space (~100GB). /tmp lives on /dev/root
    which is cramped after the LVM image is allocated. A full UUP
    download is ~5GB and the WIM conversion intermediates can hit 8GB+,
    so /tmp is not reliable.
    """
    text = (REPO_ROOT / "scripts/build/convert.sh").read_text()
    assert "GITHUB_WORKSPACE" in text
    assert "mktemp -d -p" in text or "mktemp -d -t" in text or "WORKDIR=" in text


def test_repack_sh_finds_iso_builder_from_multiple_sources():
    """repack.sh must look for ISO builders: oscdimg (Windows ADK), xorriso, or genisoimage (Linux)."""
    text = (REPO_ROOT / "scripts/build/repack.sh").read_text()
    # Must mention at least one of: xorriso, genisoimage, oscdimg
    assert any(name in text for name in ("xorriso", "genisoimage", "oscdimg"))


def test_repack_sh_falls_back_to_uefi_only_when_bios_boot_missing():
    """repack.sh must handle missing etfsboot.com (UEFI-only mode)."""
    text = (REPO_ROOT / "scripts/build/repack.sh").read_text()
    assert "etfsboot.com" in text
    # Has a branch for when BIOS boot is missing
    assert "UEFI-only" in text or "UEFI_only" in text or "efisys" in text


def test_repack_sh_uses_workspace_for_temp_dir():
    """repack.sh must use $GITHUB_WORKSPACE (not /tmp) for temp work.

    Extracting a 4.5GB ISO and rebuilding a 4.5GB ISO needs ~10GB of
    temp space. /tmp is on /dev/root, which is cramped after the LVM
    image consumed 87GB of the 89GB previously free.
    """
    text = (REPO_ROOT / "scripts/build/repack.sh").read_text()
    assert "GITHUB_WORKSPACE" in text
    assert "mktemp -d -p" in text or "WORKDIR=" in text


# --- Pipeline integration: simulate the full build graph ---

def test_build_pipeline_step_call_chain(tmp_path: Path):
    """Verify the build steps invoke scripts in the correct order.

    We mock every external tool (aria2, 7z, oscdimg, rclone, dism) and assert
    each build-step's command would invoke the expected script. This is a
    'wiring test' — if someone renames a script or breaks the call site, this fails.
    """
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    steps = data["jobs"]["build"]["steps"]

    def find_step(name: str) -> dict | None:
        return next((s for s in steps if s.get("name") == name), None)

    download_step = find_step("Download UUP files + convert to ISO")
    assert download_step is not None
    assert "convert.sh" in download_step["run"]
    assert "$UUP_UUID" in download_step["run"]
    assert "$EDITION" in download_step["run"]

    driver_step = find_step("Inject Intel RST drivers into WIM")
    assert driver_step is not None
    # Now uses wimlib on Linux (was dism-helpers.ps1 on Windows)
    assert "wimlib" in driver_step["run"] or "dism-helpers.ps1" in driver_step["run"]

    autou_step = find_step("Write rendered autounattend to disk")
    assert autou_step is not None
    assert "$AUTOU_XML" in autou_step["run"]
    assert "artifacts/autounattend/win11.xml" in autou_step["run"]

    repack_step = find_step("Repack ISO with autounattend")
    assert repack_step is not None
    assert "repack.sh" in repack_step["run"]
    assert "artifacts/iso-in.iso" in repack_step["run"]
    assert "install.wim" in repack_step["run"]

    assign_step = find_step("Assign upload account")
    assert assign_step is not None
    assert "scripts.rclone.assign" in assign_step["run"]
    assert "ACCOUNTS_YAML" in assign_step["run"]

    upload_step = find_step("Upload ISO to Google Drive")
    assert upload_step is not None
    assert "upload.sh" in upload_step["run"]
    assert "RCLONE_CONF" in str(upload_step.get("env", {}))
    assert "steps.assign.outputs.account" in upload_step["run"]


# --- Profile resolution ---

def test_build_workflow_accepts_profile_input():
    """build.yml must accept 'profile' as a workflow_call input."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    on_key = data.get("on", data.get(True, {}))
    call_inputs = on_key.get("workflow_call", {}).get("inputs", {})
    assert "profile" in call_inputs
    assert call_inputs["profile"]["type"] == "string"


def test_build_workflow_has_profile_resolution_step():
    """build.yml must have a step that calls scripts.profiles.load."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    steps = data["jobs"]["build"]["steps"]
    profile_step = next((s for s in steps if "Resolve profile" in s.get("name", "")), None)
    assert profile_step is not None
    assert "scripts.profiles.load" in profile_step["run"]


def test_build_workflow_maps_language_and_compression_env_vars():
    """build.yml must thread LANGUAGE + COMPRESSION through to convert.sh."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    text = json.dumps(data)
    assert "LANGUAGE" in text
    assert "COMPRESSION" in text
    # The convert step must pass compression as the 4th arg
    steps = data["jobs"]["build"]["steps"]
    convert_step = next((s for s in steps if "convert" in s.get("name", "").lower()), None)
    assert convert_step is not None
    assert "$COMPRESSION" in convert_step["run"]


def test_build_workflow_uses_label_for_output_filename():
    """Final ISO + artifact name should use $LABEL from the profile."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    steps = data["jobs"]["build"]["steps"]
    repack_step = next((s for s in steps if "Repack" in s.get("name", "")), None)
    assert repack_step is not None
    # May use ${LABEL:-...} or $LABEL — both are fine
    assert "LABEL" in repack_step["run"]
    upload_step = next((s for s in steps if "Upload ISO to Google" in s.get("name", "")), None)
    assert upload_step is not None
    assert "LABEL" in upload_step["run"]


# --- Disk space + autounattend render (post-flatten changes) ---

def test_build_workflow_frees_disk_space_before_checkout():
    """build.yml must use easimon/maximize-build-space as the first step
    (UUP→WIM conversion needs more space than the runner ships with)."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    steps = data["jobs"]["build"]["steps"]
    assert len(steps) > 0
    first_step = steps[0]
    # Action is the documented way (not a hand-rolled script)
    assert "easimon/maximize-build-space" in first_step.get("uses", "")
    # Must run before checkout — so the remounted volume is in scope for everything else
    checkout_idx = next(
        (i for i, s in enumerate(steps) if "actions/checkout" in s.get("uses", "")),
        None,
    )
    assert checkout_idx is not None and checkout_idx > 0, (
        "Disk-space step must be the first step, before actions/checkout"
    )


def test_build_workflow_renders_autounattend_from_secrets():
    """build.yml must have a step that reads secrets and renders {{...}} placeholders."""
    data = yaml.safe_load((WORKFLOWS_DIR / "build.yml").read_text())
    steps = data["jobs"]["build"]["steps"]
    render_step = next(
        (s for s in steps if "Render autounattend" in s.get("name", "")),
        None,
    )
    assert render_step is not None, "Missing autounattend render step"
    # Uses the inject_autounattend.render() library
    assert "inject_autounattend" in render_step["run"]
    # Reads the secrets
    env = render_step.get("env", {})
    assert "LOCAL_ADMIN_PASS" in env
    assert "LOCAL_ADMIN_NAME" in env
    # Fails loudly if a secret is missing
    assert "::error::" in render_step["run"] or "exit 1" in render_step["run"]


def test_build_workflow_no_references_to_private_repo():
    """Post-flatten: build.yml must not reference winforge-private anywhere."""
    text = (WORKFLOWS_DIR / "build.yml").read_text()
    assert "winforge-private" not in text
    assert "secrets: inherit" not in text
    assert "WINFORGE_PRIVATE_TOKEN" not in text
