# Changelog

## 1.0.0

First release.

- **Queue:** add photos (JPG, PNG, WEBP, BMP, TIFF, GIF) and videos (MP4, MOV) by file dialog or drag and drop. Each gets the next free default posting time; a calendar picker, +1 hour, +1 day and Next slot buttons change it. Caption with a live character and hashtag check.
- **Posting:** at the scheduled minute the photo is prepared for Instagram (orientation, transparency, 4:5 to 1.91:1 shape by crop or white borders, 320 to 1440 px wide, under 8 MB), uploaded to the user's media hosting, published through Meta's Graph API, and the hosted copy is removed. Videos are published as Reels. Transient failures retry three times, five minutes apart.
- **Missed posts:** if the PC was off, a post still goes out within a grace period (12 hours by default); older ones are marked missed and can be rescheduled or posted now.
- **Accounts:** sign in with the browser (Facebook Login or Instagram Login) or paste a token; the account is looked up and shown. Tokens are protected with Windows DPAPI. Instagram Login tokens are refreshed before they expire; others show a warning a week ahead.
- **Runs in the background:** closing the window keeps PostQueue in the notification area, a second launch brings the window back, and the installer adds a start-at-sign-in entry (Settings > General turns it off).
- **Simple setup:** with the built-in Meta app and the PostQueue service (`service\`, a Cloudflare Worker that finishes the sign-in and hosts photos for a moment) configured in `branding.py`, the first run is three screens: professional account, sign in, posting time. No developer registration, no hosting account. The advanced path stays behind a link.
- **First run:** a full-screen setup in the style of Ollama's installer: large type, one instruction and one frosted-glass button per screen, seventeen short screens at most (professional account, how to sign in, create the Meta app, App ID, App secret, redirect address, tester role, sign in, which account, Cloudinary account, cloud name, API key, API secret, test hosting, posting time, start at sign-in). The sign-in and the hosting test move on by themselves when they succeed; Next only lights up once a field has a value; a token shortcut skips the Meta app screens. The tabbed Settings dialog serves later changes.
- **Date picking:** the scheduled date is a button that opens a calendar, with the time next to it and +1 hour, +1 day, +1 week and Next free slot underneath.
- **Look:** the main window, the Settings dialog and the calendar share the setup screens' style: a soft gradient behind everything, frosted-glass buttons and fields, larger type, and Save changes and Post now brighter than the other buttons.
- **Package, Windows:** one-folder build, portable EXE, zip and per-user or per-machine MSI for x64 and ARM64, following the Local AI Manager build guideline. The MSI shows one screen with an Install button.
- **Package, Mac:** `PostQueue.app` in a disk image with an Applications link (one drag to install) for Apple silicon and Intel, built by `build_macos.sh`; secrets in the login keychain, start at sign-in as a LaunchAgent.
