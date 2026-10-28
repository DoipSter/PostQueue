# Changelog

## 1.1.0

More kinds of posts, and less typing.

- **Carousels:** add several photos or videos at once and choose "One carousel post" (2 to 10 items, swiped through on Instagram), or add another photo to an existing post with **Add another photo or video**. The strip under the preview shows the items; **Remove selected** takes one out. Videos in a carousel are posted as videos, not Reels.
- **Stories:** **Post as › Story** publishes a photo or video as a story (9:16, no caption, gone after 24 hours). Photos are cropped or padded to 9:16 the same way feed photos are fitted, and the preview shows it.
- **First comment:** text posted as the first comment right after publishing, where many people keep their hashtags. If the comment fails, the post still counts as posted and the strip says so.
- **Alt text** for photos, and a **cover moment** for videos (which second of the video becomes its cover picture).
- **Drafts and Duplicate:** tick **Draft** to keep a post without scheduling it; **Duplicate** copies a post with its photos and settings onto the next free slot.
- **Add from a sheet:** a CSV with the columns `file`, `date`, `time`, `caption`, `first_comment`, `alt_text`, `target` adds a whole plan at once; `file` may hold several paths separated by `|` for a carousel, and paths are relative to the sheet. Rows that cannot be added are listed, the rest go in.
- **New versions:** on start, PostQueue checks the Releases page once and shows a blue strip with a Download button when a newer version exists (Settings › General turns it off).
- **Sign-in asks only for the permissions App Review covers.**
- **Setup checks as you go (advanced path):** the App ID is looked up at Meta when you paste it (public apps show their name; apps in development mode are confirmed on the next screen), the App secret is proved against the ID before moving on, and a failed sign-in says exactly what to fix (the redirect address, or adding yourself as a tester). The developer site now opens at its front page, since its apps list shows "not found" while logged out.

## 1.0.0

First release.

- **Queue:** add photos (JPG, PNG, WEBP, BMP, TIFF, GIF) and videos (MP4, MOV) by file dialog or drag and drop. Each gets the next free default posting time; a calendar picker, +1 hour, +1 day and Next slot buttons change it. Caption with a live character and hashtag check.
- **Posting:** at the scheduled minute the photo is prepared for Instagram (orientation, transparency, 4:5 to 1.91:1 shape by crop or white borders, 320 to 1440 px wide, under 8 MB), uploaded to the user's media hosting, published through Meta's Graph API, and the hosted copy is removed. Videos are published as Reels. Transient failures retry three times, five minutes apart.
- **Missed posts:** if the PC was off, a post still goes out within a grace period (12 hours by default); older ones are marked missed and can be rescheduled or posted now.
- **Accounts:** sign in with the browser (Facebook Login or Instagram Login) or paste a token; the account is looked up and shown. Tokens are protected with Windows DPAPI. Instagram Login tokens are refreshed before they expire; others show a warning a week ahead.
- **Runs in the background:** closing the window keeps PostQueue in the notification area, a second launch brings the window back, and the installer adds a start-at-sign-in entry (Settings > General turns it off).
- **Simple setup:** with the built-in Meta app and the PostQueue service (`service\`, a Cloudflare Worker that finishes the sign-in and hosts photos for a moment) configured in `branding.py`, the first run is three screens: professional account, sign in, posting time. No developer registration, no hosting account. The advanced path stays behind a link.
- **First run:** a full-screen setup in the style of Ollama's installer: large type, one instruction and one frosted-glass button per screen, seventeen short screens at most (professional account, how to sign in, create the Meta app, App ID, App secret, redirect address, tester role, sign in, which account, Cloudinary account, cloud name, API key, API secret, test hosting, posting time, start at sign-in). The sign-in and the hosting test move on by themselves when they succeed; Next only lights up once a field has a value; a token shortcut skips the Meta app screens. The tabbed Settings dialog serves later changes.
- **Confirmation:** after a post goes out, a green strip at the top of the window says what was posted and when, with Open on Instagram; a failed post shows a red strip with the reason. Both stay until dismissed. `docs\walkthrough` shows every screen from first launch to that confirmation.
- **Date picking:** the scheduled date is a button that opens a calendar, with the time next to it and +1 hour, +1 day, +1 week and Next free slot underneath.
- **Crop preview:** the preview shows what Instagram will get. With Crop to fit Instagram, the strips that are cut off are dimmed and the kept part is outlined; with Add white borders, the photo sits on its white frame. The preview and the real crop use the same arithmetic (`target_box` in `engine.py`), and a line under the preview says what will happen.
- **Video checks when adding:** the MP4/MOV header is read as the file is added (no new dependencies). Videos over 15 minutes or under 3 seconds are refused with a plain message; a landscape or very tall video gets an amber note in the details pane (Reels are shown at 9:16), and a file whose header cannot be read gets a one-line note. The details pane shows a video's length and size.
- **Post now asks first:** Post now confirms ("Post name to Instagram now?") before publishing, like Remove does.
- **Look:** the main window, the Settings dialog and the calendar share the setup screens' style: a soft gradient behind everything, frosted-glass buttons and fields, larger type, and Save changes and Post now brighter than the other buttons.
- **Package, Windows:** one-folder build, portable EXE, zip and per-user or per-machine MSI for x64 and ARM64, following the Local AI Manager build guideline. The MSI shows one screen with an Install button.
- **Package, Mac:** `PostQueue.app` in a disk image with an Applications link (one drag to install) for Apple silicon and Intel, built by `build_macos.sh`; secrets in the login keychain, start at sign-in as a LaunchAgent.
