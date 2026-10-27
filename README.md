# PostQueue 1.0

A Windows and Mac app that posts photos and videos to Instagram on a schedule. Drop files in, pick the date and time for each, write the caption, and PostQueue publishes them through Meta's official Instagram API while it sits in the notification area or menu bar. Installing is one click (Windows) or one drag (Mac), and the first run asks for one thing per screen.

- **Picture by picture:** [docs/walkthrough](docs/walkthrough/README.md) (every screen from first launch to a posted photo and its confirmation)
- **Users:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (install, one-time setup, daily use)
- **Privacy and data deletion:** [docs/privacy.md](docs/privacy.md) and [docs/data-deletion.md](docs/data-deletion.md), published at <https://doipster.github.io/PostQueue/privacy/> and <https://doipster.github.io/PostQueue/data-deletion/> (GitHub Pages from `docs\`; these are the addresses Meta's App Review asks for)
- **Meta App Review:** [docs/app-review.md](docs/app-review.md) (permissions, screen-recording script, business verification)
- **Build and release testing:** [BUILD_AND_TEST_GUIDE.md](BUILD_AND_TEST_GUIDE.md)
- **What changed:** [CHANGELOG.md](CHANGELOG.md)

## How it works

Instagram's API has no file upload for photos and no built-in scheduling. So PostQueue:

1. keeps the queue on this PC and runs in the background (started at sign-in by the installer);
2. at the scheduled minute, prepares the photo (shape, size, format), uploads it to the user's own media hosting account (Cloudinary, free tier) so Instagram can fetch it from a public link, creates and publishes the post, then removes the hosted copy;
3. publishes videos as Reels the same way.

One-time setup, once `branding.py` names your Meta app and the deployed PostQueue service (`service\`, a Cloudflare Worker that finishes the sign-in and hosts each photo for a moment), is three screens: professional account, sign in, posting time. Without them, the app falls back to the advanced path, where the user creates their own Meta app and Cloudinary account. `BUILD_AND_TEST_GUIDE.md` section 7 explains the publishing setup.

## Project layout

| Path | Contents |
|---|---|
| `PostQueue.pyw`, `app.py` | Entry point and the Qt interface (queue, details pane, tray icon, first-run wizard, settings dialog, scheduler timer) |
| `engine.py` | The queue (`posts.json` and media copies), timing rules, photo preparation, publishing steps and retries |
| `instagram.py` | Graph API client (Facebook Login and Instagram Login hosts), token exchange, browser sign-in receiver |
| `hosting.py` | Media hosting: the PostQueue service, or Cloudinary uploads (signed, chunked for large videos), and removal |
| `branding.py`, `service\` | The built-in Meta app ID and service address, and the service itself (Cloudflare Worker: token exchange, temporary media hosting, hourly cleanup) |
| `settings.py` | Settings file with protected secrets (DPAPI on Windows, keychain on macOS), data folder, start at sign-in (Run entry or LaunchAgent), single-instance lock |
| `version.py` | Product version (the single source) |
| `tests\` | Engine and interface tests (pytest, no network), `release\Test-Release.ps1` and `release\test-release-macos.sh` for release builds |
| `installer\`, `assets\` | Package definition (PyInstaller spec for both platforms), MSI definition (WiX v5) and its Install screen text, icons (`make_assets.py` regenerates them, including the `.icns`) |
| `Build_Windows_EXE.cmd`, `build_macos.sh` | The two build scripts |
| `LICENSE`, `THIRD_PARTY_NOTICES.txt`, `licenses\` | PostQueue's MIT licence; third-party notices and licence texts, shipped with the program |
| `docs\` | User guide, walkthrough, privacy policy, data deletion page and App Review notes; `_config.yml` and `index.md` make it a GitHub Pages site |
| `.github\workflows\build.yml` | CI: Windows x64 and ARM64 (MSIs) and macOS arm64 and x86_64 (DMGs), tests and release tests |

## Build

Windows (x64 or ARM64, matching the Python used):

```bat
py -m pip install -r requirements-build.txt
Build_Windows_EXE.cmd
```

Mac (Apple silicon or Intel, matching the Mac):

```bash
python3 -m pip install -r requirements-build.txt
./build_macos.sh
```

See the build guide for x64 builds on ARM PCs, the MSI and DMG, and signing.

## Data

Everything the app keeps is in `%LOCALAPPDATA%\PostQueue`: `posts.json` (the queue), `media\` (copies of the files, so the originals can be moved), `settings.json` (tokens and secrets protected with Windows DPAPI for the signed-in user), and `postqueue.log`. The `POSTQUEUE_DATA` environment variable points the app elsewhere.

Silent install: `msiexec /i PostQueue-1.0.0-x64.msi /qn` installs per user. Add `ALLUSERS=1` to install per machine.

## Licence

PostQueue is released under the MIT licence; see [LICENSE](LICENSE). The bundled components (Python, Qt, Pillow and the rest) keep their own licences, listed in `THIRD_PARTY_NOTICES.txt` and `licenses\`.
