# Connect Vercel to Hermes

## Owner-specific values

The VPS bridge has been installed and tested. Your settings are stored **outside this repository** on the Windows computer:

```text
%LOCALAPPDATA%\HermesDashboard\vercel-settings.txt
%LOCALAPPDATA%\HermesDashboard\vercel-private-key.txt
```

The first file has the VPS host, username and verified SSH fingerprint. The second has a **dedicated restricted key**, not the administrator key. Never upload either file to GitHub.

## Import into Vercel

1. Add New → Project → Import this repository.
2. Leave **Root Directory** at `./`.
3. Choose **Other** for the framework and Node.js **22.x**. The tracked configuration specifies `npm run build` and output directory `dist`.
4. Add these **Environment Variables for Production only**:

| Variable | Value |
| --- | --- |
| `HERMES_SSH_HOST` | From the private settings file |
| `HERMES_SSH_USER` | From the private settings file |
| `HERMES_SSH_HOST_SHA256` | Exact 64-character hexadecimal host-key hash from the private settings file |
| `HERMES_SSH_PRIVATE_KEY` | Complete multiline contents of the private key file, including its BEGIN/END lines |
| `HERMES_PUBLIC_ORIGIN` | Your stable production URL, such as `https://your-project.vercel.app`, without a trailing slash |

5. Deploy. If the production URL differs from your initial guess, update `HERMES_PUBLIC_ORIGIN` and **redeploy**. Use the stable project URL, not a unique deployment URL.
6. Sign in with the existing dashboard password. No terminal or SSH app is needed on your phone or other laptop.

Do not expose Production secrets to untrusted Preview deployments. The proxy also rejects requests on hostnames other than `HERMES_PUBLIC_ORIGIN`.

## Verify the deployed site

- A logged-out request to `/api/status` returns 401.
- The correct password opens live workflow data.
- Errors, plans and saved reports match the VPS.
- Login cookies have `HttpOnly`, `SameSite=Strict` and `Secure`.
- Open the production URL on another device.

The local build and SSH bridge can be checked before deployment. Vercel routing and network access require testing the actual deployment. A sign-in page alone does not prove the VPS connection works.

## Reproduce VPS preparation

Deploy `server.py`, `bridge.py` and `static/` under `~/hermes-control/` on the VPS. Create `~/.local/state/hermes-control/auth.json` with the password's salt and scrypt hash and install the supplied user service. Use a trusted existing SSH connection; do not automatically accept a changed host-key warning.

With dependencies installed, run locally:

```sh
node scripts/prepare-vercel.mjs --host YOUR_VPS_IP --user ubuntu --admin-key PATH_TO_EXISTING_ADMIN_KEY
```

The helper generates a dedicated Ed25519 key outside the repository, adds one `restrict,command="...bridge.py"` entry to `authorized_keys`, creates a bridge token in the private backend auth file, and records the verified Ed25519 host identity. Existing authorized keys are preserved. On Windows it restricts the credential directory to your Windows account. Keep that directory restricted on other operating systems too.

Restart the backend after setup:

```sh
systemctl --user restart hermes-control
systemctl --user is-active hermes-control
```

The private API stays on loopback. No new public firewall port or domain is needed. SSH must remain reachable from Vercel, and the VPS must remain online.

## Operations

- Requests have bounded timeouts. A timeout after pause/resume is an unknown result: refresh before retrying.
- Public errors never include detailed SSH output, keys or report contents.
- Restarting the backend clears sessions; sign in again.
- Public login throttling uses hashed client IPs authenticated to the backend with a bridge token. Devices sharing a public IP share a limit.
- The key still requires a dashboard password/session for private API access. To revoke it, remove its specific `hermes-vercel-bridge` entry from `authorized_keys` and delete the Vercel secret.
- Vercel Hobby limits apply. The existing local SSH launcher remains a fallback.
