# Gofile Upload

`scripts.gofile.upload` pushes finished ISOs to [gofile.io](https://gofile.io) and
returns a public download page. Runs as the final step of the build workflow,
alongside the existing rclone/Google Drive upload.

## Two modes

| Mode | Trigger | Folder layout | Expiry | Requires |
|------|---------|---------------|--------|----------|
| Guest | no `GOFILE_TOKEN` | server-generated `Guest-XXXXX/` | ~10 days (server-side) | nothing |
| Account | `GOFILE_TOKEN` secret set | `/{rootFolder}/{product}/{edition}/` | never (until you delete) | free gofile account + JWT |

## Getting a JWT (account mode)

1. Create a free gofile account at <https://gofile.io>
2. Sign in, open DevTools → Network → any API request
3. Copy the `Authorization: Bearer ...` value (it's a long JWT)
4. Store as the `GOFILE_TOKEN` Actions secret on this repo

That's it. No Google, no OAuth, no service account.

## CLI usage

```bash
# Guest (no token)
python -m scripts.gofile.upload path/to/win11.iso \
  --product win11-24h2 --edition professional

# Account
GOFILE_TOKEN=eyJhbGciOi... python -m scripts.gofile.upload path/to/win11.iso \
  --product win11-24h2 --edition professional
```

Output (key=value lines on stdout, easy to shell-capture):

```
folder_url=https://gofile.io/d/B72iSS
file_id=f0f2f95d-...
folder_id=87be0978-...
direct_url=https://store1.gofile.io/dl/...
guest_token=...                # only for guest uploads
```

## API endpoints used

```
GET  https://api.gofile.io/servers
GET  https://api.gofile.io/accounts/getid          (auth)
POST https://api.gofile.io/contents/createFolder   (auth)
POST https://{server}.gofile.io/contents/uploadfile
```

## Workflow wiring

`.github/workflows/build.yml` runs the upload step unconditionally — if
`GOFILE_TOKEN` is empty it just logs a warning and uploads as guest.

```yaml
- name: Upload ISO to gofile.io
  env:
    GOFILE_TOKEN: ${{ secrets.GOFILE_TOKEN }}
  run: |
    python -m scripts.gofile.upload \
      "artifacts/win11-${EDITION}.iso" \
      --product "$PRODUCT" \
      --edition "$EDITION"
```

## Rate limits

Gofile says don't call `/servers` more than once per 10 seconds. The current
script calls it once per build, which is well within bounds. If you parallelize
builds heavily, cache the server list for the job run.

## Testing

```bash
pytest tests/test_gofile.py -q
```

All network calls are mocked — CI never hits gofile.
