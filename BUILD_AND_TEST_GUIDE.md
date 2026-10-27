# PostQueue 1.0: building and testing the package

This guide covers producing and testing the Windows package (sections 1 to 6), the one-time publishing setup that gives users the simple sign-in (section 7), and the macOS package (section 8): the build machine, the build steps, signing, the installer, and the tests that show a build is good. It follows the same packaging guideline as Local AI Manager. For what the app does, see `docs/USER_GUIDE.md`.

Each build produces, in `dist\`, with `NAME = PostQueue-<version>-<arch>`:

| Output | Use |
|---|---|
| `NAME\` | **One-folder build (recommended).** Starts fastest and does not unpack anything to `%TEMP%`, which suits AppLocker/WDAC. The MSI installs this. |
| `NAME.zip` | The one-folder build, zipped |
| `NAME.exe` | Portable single file. It unpacks to `%TEMP%` each time it starts. |
| `NAME.msi` | Windows Installer package: per-user by default, per-machine with `ALLUSERS=1` (built when WiX is available). Adds the Start menu shortcut and the start-at-sign-in entry. |
| `*.sha256.txt` | SHA-256 of each EXE, zip and MSI |
| `THIRD_PARTY_NOTICES.txt`, `licenses\` | Third-party notices and licence texts. Ship them with every download. The program carries its own copy in `_internal`. |

`<arch>` is `x64` (Intel/AMD, and ARM64 under emulation) or `arm64` (ARM64 only). The version comes from `version.py`, which is the only place it is set.

## 1. Build machine

| Requirement | Detail |
|---|---|
| Windows | 10 or 11. A normal user account is enough. |
| Python | 3.12 from python.org. **The build matches the CPU architecture of this Python.** For x64 builds on an ARM64 PC, install x64 Python for the current user and keep `py` on ARM64 with `%LOCALAPPDATA%\py.ini` containing `[defaults]` and `python=3.12-arm64`. |
| Packages | `py -m pip install -r requirements-build.txt` installs PySide6-Essentials 6.9.1, Pillow 13.0.0, PyInstaller 6.11.1 and pytest 8.3.4 (all pinned). |
| MSI (optional) | WiX Toolset v5. Without administrator rights: install the .NET 8 SDK for the current user with Microsoft's signed `dotnet-install.ps1 -Channel 8.0 -InstallDir %LOCALAPPDATA%\LocalAIManagerBuild\dotnet -NoPath`, then `dotnet tool install wix --version 5.0.2 --tool-path %LOCALAPPDATA%\LocalAIManagerBuild\wix`, then `%LOCALAPPDATA%\LocalAIManagerBuild\wix\wix.exe extension add -g WixToolset.UI.wixext/5.0.2` for the Install screen. The same WiX install serves both products. |
| Signing (optional) | Either a code-signing certificate in the Windows certificate store, or an Azure Trusted Signing account. `signtool.exe` (Windows SDK) must be on PATH. |

## 2. Build

```bat
cd C:\Build\PostQueue
py -m pip install -r requirements-build.txt
set DOTNET_ROOT=%LOCALAPPDATA%\LocalAIManagerBuild\dotnet
set WIX=%LOCALAPPDATA%\LocalAIManagerBuild\wix
Build_Windows_EXE.cmd
```

For the other architecture, set `PY` to that Python (for example `set PY="%LOCALAPPDATA%\Programs\Python\Python312-x64\python.exe"`) and run the script again. To run unattended, as in CI, use `cmd /c "Build_Windows_EXE.cmd < nul"`.

The script does the following, stopping at the first failure:

1. Checks the tools and reads the version.
2. Runs all tests (`tests\`: engine and interface).
3. Builds the one-folder build. It is built in `%TEMP%` and copied into `dist\` with retries, because cloud-synced folders briefly lock new files.
4. Builds the portable EXE.
5. Signs both, if signing is set up.
6. Zips the one-folder build.
7. Builds and signs the MSI, if `WIX` is set.
8. Copies the third-party notices and licences, and writes the SHA-256 files.

What goes into the package is defined in `installer\PostQueue.spec`:
- **Data files:** icons, licences and notices.
- **Unused Qt parts** that are left out (the `UNUSED` list). Photos are decoded with Pillow, so Qt's image plugins are not needed.
- **`__future__`**, which PySide6 6.9 needs. Without it, the packaged app crashes at start-up with exception 0xc0000409, and nothing is shown.

The MSI (`installer\PostQueue.wxs`) shows one screen with an **Install** button (WixUI_Minimal with `installer\License.rtf`; `/qn` still installs silently), installs the one-folder build, a Start menu shortcut, and a per-user Run entry named `PostQueue Startup` (`"<exe>" --minimized`) so the queue is served after sign-in. Each architecture has its own fixed UpgradeCode in `Build_Windows_EXE.cmd`, so later versions replace earlier ones. Inside the app, the first run opens a wizard that asks for one thing per screen (Instagram, hosting, posting time), so the installer never has to.

**The app never deletes that Run entry.** Settings > General turns start-at-sign-in off by marking the entry disabled under `Explorer\StartupApproved\Run`, which is what Task Manager does. Found while testing 1.0.0: once a Run value written by an MSI has been deleted by hand and the product uninstalled, Windows Installer silently stops creating a value of that name for the same user (the verbose log still shows `RegAddValue`, nothing lands, other names and user-mode writes work). Keep the value name and the no-delete rule; if a test PC ever shows the entry missing right after install, the name has been burned on that PC.

## 3. Signing

Unsigned builds work, but SmartScreen warns, some antivirus products flag them, and Application Control (WDAC/AppLocker) may block them.

- **Certificate in the Windows store:** run `set SIGN_CERT_THUMBPRINT=<40 hex characters>` before building.
- **Azure Trusted Signing:** run `set SIGN_DLIB=<path to Azure.CodeSigning.Dlib.dll>` and `set SIGN_METADATA=<path to metadata.json>`. In CI, store these as repository secrets, which the workflow passes through.
- **Check a signed build:** run `Get-AuthenticodeSignature dist\<file>`. Expect `Valid`, your organization as the signer, and a timestamp.

## 4. Tests

| Level | How | What it covers |
|---|---|---|
| Engine | `py -m pytest tests\test_engine.py` (run by the build) | Queue storage and reload, due and missed rules, retry timing, next free slot, caption limits, photo preparation (shape, size, transparency, orientation, size limit), the publishing steps with simulated Instagram and hosting (photo, video processing, transient and permanent failures, cleanup), settings secret protection, the instance lock, Cloudinary signatures and chunked upload, Graph API error parsing, account lookup for both login kinds, sign-in URLs, token exchange and the browser sign-in receiver. No network. |
| Interface | `py -m pytest tests\test_gui.py` (run by the build; off-screen) | Empty state and setup banner, adding by dialog and by drop, next-slot scheduling, editing caption and time, saving, Post now, the scheduler timer (due and missed), pause, failure display, removal, the settings dialog (hosting, general, token check, browser sign-in, hosting test), second-launch handling, close behaviour. |
| Release | `powershell -ExecutionPolicy Bypass -File tests\release\Test-Release.ps1 -Dist dist -Out release-results` | For every EXE, one-folder build and MSI: CPU type, recorded hash, start, lock, second launch restores the window, clean close, crash and relaunch, start from a folder with accented and Chinese characters, notices present, and the MSI's install without administrator rights, shortcut, start-at-sign-in entry, the app turning it off without deleting it, and clean uninstall. Needs no Python. The app is pointed at a data folder under `release-results` so the tester's own queue is untouched, with `setup_done` set so the first-run wizard (a modal dialog, which would block the close message) stays out of the way. Run the architectures one after the other: both MSIs install to the same folder. |
| Clean machine | Run the release tests in Windows Sandbox or on a fresh VM | The release tests on a fresh Windows with nothing installed |
| Real posting | Manual, with a test Instagram account | Settings > Test hosting; sign in; add a photo and a short video; Post now; check both on Instagram; check the hosted copies were removed in the Cloudinary media library. |

**Release gate.** The release tests must pass on a clean machine for each architecture, plus real Intel/AMD hardware for x64. Before release, also run one real posting test (photo and video), scan `dist\` with Microsoft Defender, and install the MSI per machine (`ALLUSERS=1`).

## 5. CI

`.github/workflows/build.yml` builds x64 on `windows-latest` and ARM64 on `windows-11-arm`. It installs the pinned tools and WiX v5, runs the build (which runs the tests), runs the release tests, and uploads the outputs and results. Signing runs when the signing secrets are present.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| The packaged app shows nothing, and the event log shows 0xc0000409 in `ucrtbase.dll` | A module is missing from the bundle. Build a console variant (`--console`) to see the error. For PySide6 6.9 it is `__future__`. |
| "Access is denied" removing `dist\...` | OneDrive is still syncing. The script retries; otherwise wait and run it again. |
| `FileNotFoundError ... localpycs` | The path is too long. Use a short folder such as `C:\Build\PostQueue`. |
| The MSI fails with 1619 | msiexec was given a relative path. Use the full path. |
| An x64 build comes out as ARM64 | Set `PY` to the x64 `python.exe`. |
| "MSI skipped" | Set `WIX` (and `DOTNET_ROOT` if the SDK was installed per user). |
| The release tests' "Second launch restores open window" fails | The first instance watches for `show.request` in its data folder every 1.5 s and the second launch also calls `ShowWindow`; a very slow PC may need the 2.5 s wait in the script raised. |
| Photos post but videos fail with "unsupported format" | Instagram wants MP4/MOV with H.264 video and AAC audio. Re-encode (for example with HandBrake) and add the file again. |

## 7. Publishing setup: the built-in Meta app and service

Out of the box, PostQueue's setup is the **advanced** path: the user creates their own Meta app and Cloudinary account (about fifteen screens). To ship the **simple** path, where the user only signs in and picks a posting time, do this once:

1. **Register one Meta app** at <https://developers.facebook.com/apps/> (Business type; add the Instagram product and Facebook Login; redirect URI `http://localhost:48215/`). Note the App ID and App secret.
2. **Deploy the PostQueue service** from `service\` (a Cloudflare Worker with R2 storage, free plan): `service\README.md` has the five commands. It holds the app secret, finishes the sign-in with a 60-day token, and hosts each photo for the moment Instagram needs it.
3. **Fill in `branding.py`**: `META_APP_ID` and `SERVICE_URL`. Build. `branding.simple_setup()` is now true, so the setup flow shows three screens, Settings > Media hosting offers "PostQueue hosting (built in)", and the welcome screen keeps an **Advanced** link for people who want their own app and hosting.
4. **App Review.** While the Meta app is in development mode, only its listed testers can sign in, which is enough for you and a few testers. For the public, submit the app for review with `instagram_basic`, `instagram_content_publish`, `pages_show_list` and `pages_read_engagement`, a privacy policy and a screen recording, and complete business verification. `docs\app-review.md` has the submission notes: one paragraph per permission, the recording script, the verification checklist and the public addresses of the privacy policy and the data deletion page.

The environment variables `POSTQUEUE_META_APP_ID` and `POSTQUEUE_SERVICE_URL` override `branding.py` for a build or a test run without editing the file.

## 8. macOS

The same source builds `PostQueue.app` on a Mac. What differs is in `settings.py`: secrets go to the login keychain (items named `PostQueue <field>`, written with the `security` tool) instead of DPAPI, start at sign-in is a LaunchAgent (`~/Library/LaunchAgents/com.postqueue.app.plist`) instead of the Run entry, and the data folder is `~/Library/Application Support/PostQueue`. The tray icon lives in the menu bar; clicking the Dock icon brings the window back.

| Requirement | Detail |
|---|---|
| Mac | macOS 12 or later. The build matches the Mac's CPU: Apple silicon gives `arm64`, Intel gives `x86_64`. PyInstaller cannot cross-compile, so build each on its own kind of Mac (CI uses `macos-latest` and `macos-15-intel`). |
| Python | 3.12 from python.org (`python3`), with `python3 -m pip install -r requirements-build.txt`. |
| Signing (optional) | A Developer ID Application certificate in the keychain (`SIGN_IDENTITY`), and for notarization a `notarytool` keychain profile (`NOTARY_PROFILE`, made with `xcrun notarytool store-credentials`). Unsigned builds work, but Gatekeeper asks for a right-click > Open on first launch. |

```bash
python3 -m pip install -r requirements-build.txt
./build_macos.sh
```

The script runs the tests, builds the app from `installer/PostQueue.spec` (`PQ_MODE=app`, the `BUNDLE` branch), signs it if `SIGN_IDENTITY` is set, writes `dist/PostQueue-<version>-macos-<arch>.dmg` (the app plus an Applications link, so installing is one drag), a zip, notarizes and staples when `NOTARY_PROFILE` is set, and writes the SHA-256 files. `tests/release/test-release-macos.sh dist` then checks the bundle, the disk image, launch, lock, second launch, clean quit and crash recovery. The CI workflow does all of this for both architectures.

**Run on a Mac through CI only.** The macOS build and release scripts were written on Windows and first ran on GitHub's Apple-silicon runner (see the test record): the tests pass there, the app and disk image build, and the release checks pass. The keychain, LaunchAgent and data-folder logic is also covered by the engine tests with the `security` tool and the home folder simulated. Nobody has yet opened the app on a real Mac desktop; do that before shipping a Mac build.

## 9. Test record: 1.0.0, 2026-10-26

Built and tested on an ARM64 Windows 11 PC (build 26300), without administrator rights. The source was mirrored to a short folder under `%TEMP%` for the build because the session folder was too deep for the zip and MSI steps.

| Test | Result |
|---|---|
| Engine and interface tests (47 + 19), run by both builds | **Pass**: 66 passed in the ARM64 and the x64 build (includes the first-run wizard, and the macOS keychain, LaunchAgent and data-folder logic with the `security` tool and home folder simulated) |
| Release tests (`Test-Release.ps1`), ARM64: portable, one-folder and MSI with the Install screen | **Pass**: 33 of 33. Start 0.6–1.1 s. |
| Release tests, x64 (under emulation): portable, one-folder and MSI with the Install screen | **Pass**: 33 of 33. Start 1.4–3.4 s. |
| CPU type and hashes | **Pass**: x64 `8664`, ARM64 `AA64`, hashes match their records |
| Package contents | **Pass**: notices and licences in `_internal`; numpy, requests and the unused Qt parts are not bundled (22 MB ARM64, 29 MB x64 portable; 18 MB and 23 MB MSIs) |
| Microsoft Defender scan of `dist\` | **Pass**: no threats |
| Screens (`tools\screenshots.py`) | Reviewed: queue with photo and video thumbnails, details pane, the not-set-up banner, the three settings tabs, and the five wizard screens (`docs\screenshots\`) |
| CI, Windows x64: GitHub `windows-latest`, run #3 at commit `f6645fd` | **Pass**: 66 tests, MSI built, release tests 33 of 33 including the MSI install and clean uninstall. 2 min 59 s. |
| CI, Windows ARM64: GitHub `windows-11-arm`, run #3 at commit `f6645fd` | **Pass**: 66 tests, release tests 33 of 33. 3 min 53 s. The ARM64 runner serves this private repository. |
| CI, macOS Apple silicon: GitHub `macos-latest`, run #3 at commit `f6645fd` | 66 tests pass, `PostQueue.app` and the DMG build, release tests **13 of 14**: "Clean quit removes lock" failed because the app did not handle SIGTERM (fixed after run #3: the app now closes cleanly on it). |
| CI, run #4 at commit `94ab2b9`, all four jobs | **Pass** in 4 min 3 s overall: Windows x64 (2 min) and ARM64 (4 min) with 33 of 33 release checks each; macOS Apple silicon (1 min 38 s) and macOS Intel on `macos-15-intel` (3 min) with the app, DMG and zip built and **14 of 14** release checks each. Unsigned on all four, as no signing secrets are set. |

**Found and fixed while testing:**
- **Pillow's PyInstaller hook bundled numpy** (18 MB) and the requests stack; both are now excluded in the spec.
- **The start-at-sign-in Run entry vanished after the first install cycle.** The app used to delete the installer's Run value when the setting was off; after an uninstall, Windows Installer never created a value of that name again (see section 2). The app now disables the entry through StartupApproved instead, the value is named `PostQueue Startup`, and the release test checks that turning it off leaves the value in place.
- **A deep source path** broke the zip and MSI steps (as the troubleshooting table says). Build from a short folder.
- **WixUI_Minimal already sets `ARPNOMODIFY`**; declaring it again fails the MSI build with WIX0091.
- **The first-run wizard is modal**, so the release tests' close message was blocked on a fresh data folder. The test settings now set `setup_done`, and the script stops a process that did not close so later checks stay meaningful.

**Found and fixed through CI:**
- **macOS:** the secrets test only expected the Windows and fallback markers, not `keychain:`; worker threads parented to widgets made the interpreter crash at exit (now kept alive by attributes only, and the test session leaves with its real result on macOS); SIGTERM ended the app without releasing the lock (now handled as a clean quit).
- **CI:** the Intel Mac job targeted the retired `macos-13` runner.

**Not yet tested:**
- a real post: no Instagram professional account, Meta app or Cloudinary account was available in this session. The setup wizard's Sign in and Test hosting steps, and one photo and one video post, need a manual run before release.
- the macOS app on a real Mac desktop (CI builds and runs it headless on both Apple silicon and Intel)
- a clean Windows machine, Windows 10, a per-machine MSI install, signed builds
- display scaling other than 200%, Narrator
