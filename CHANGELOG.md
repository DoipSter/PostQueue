# Changelog

## 1.0.0

First release.

- **Queue:** add photos (JPG, PNG, WEBP, BMP, TIFF, GIF) and videos (MP4, MOV) by file dialog or drag and drop. Each gets the next free default posting time; a calendar picker, +1 hour, +1 day and Next slot buttons change it. Caption with a live character and hashtag check.
- **Posting:** at the scheduled minute the photo is prepared for Instagram (orientation, transparency, 4:5 to 1.91:1 shape by crop or white borders, 320 to 1440 px wide, under 8 MB), uploaded to the user's media hosting, published through Meta's Graph API, and the hosted copy is removed. Videos are published as Reels. Transient failures retry three times, five minutes apart.
- **Missed posts:** if the PC was off, a post still goes out within a grace period (12 hours by default); older ones are marked missed and can be rescheduled or posted now.
- **Accounts:** sign in with the browser (Facebook Login or Instagram Login) or paste a token; the account is looked up and shown. Tokens are protected with Windows DPAPI. Instagram Login tokens are refreshed before they expire; others show a warning a week ahead.
- **Runs in the background:** closing the window keeps PostQueue in the notification area, a second launch brings the window back, and the installer adds a start-at-sign-in entry (Settings > General turns it off).
- **First run:** a wizard asks for one thing per screen (Instagram account, media hosting, posting time) and only lights up Next when the step is done. The same panels serve the Settings dialog afterwards.
- **Package, Windows:** one-folder build, portable EXE, zip and per-user or per-machine MSI for x64 and ARM64, following the Local AI Manager build guideline. The MSI shows one screen with an Install button.
- **Package, Mac:** `PostQueue.app` in a disk image with an Applications link (one drag to install) for Apple silicon and Intel, built by `build_macos.sh`; secrets in the login keychain, start at sign-in as a LaunchAgent.
