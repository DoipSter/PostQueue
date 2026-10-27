# Deleting your PostQueue data

Last updated: 27 October 2026.

PostQueue keeps your data on your own computer, not on a server. So there is nothing to ask us to delete: you delete it yourself, in a few minutes, and it is gone. The [privacy policy](privacy.md) says what the data is.

## 1. Quit PostQueue

Right-click the PostQueue icon in the notification area (Windows, next to the clock) or the menu bar (Mac) and choose **Quit**. Closing the window alone is not enough: the app keeps running in the background.

If you want to keep the app but remove your sign-in, open **Settings › Instagram › Disconnect** instead and skip to step 4.

## 2. Uninstall the app (optional)

- Windows: Settings › Apps › Installed apps › PostQueue › Uninstall. (The portable `.exe` has nothing to uninstall; just delete the file.)
- macOS: drag PostQueue from the Applications folder to the Trash.

Uninstalling removes the program but leaves the data folder, so continue with step 3.

## 3. Delete the data folder

Everything PostQueue kept is in one folder: the queue, the copies of your photos and videos, your settings, the sign-in token and the log.

- **Windows:** open File Explorer, paste `%LOCALAPPDATA%\PostQueue` into the address bar and press Enter. Go up one level and delete the **PostQueue** folder. The sign-in token is inside this folder; nothing is kept anywhere else.
- **macOS:** in Finder, choose Go › Go to Folder, paste `~/Library/Application Support/PostQueue` and press Enter. Go up one level and delete the **PostQueue** folder. Then open **Keychain Access**, search for `PostQueue`, and delete the items it finds (the sign-in token lives there).

Empty the Recycle Bin or Trash if you want the files gone for good.

## 4. Remove PostQueue's access to your Instagram account

This cancels the sign-in on Meta's side, so no copy of the token could be used again.

- **Facebook** (if you signed in with Facebook Login): Settings and privacy › Settings › Apps and websites › PostQueue › **Remove**.
- **Instagram** (if you signed in with Instagram Login): Settings › Website permissions › Apps and websites › Active › PostQueue › **Remove**. The exact menu names change from time to time; look for "Apps and websites".

## What is not on your computer

- **A file being posted right now.** With the built-in sign-in, each photo or video sits on the PostQueue service only for the moment it takes Instagram to fetch it. It is deleted right after the post goes out, and anything left behind is deleted within 24 hours by an hourly job. Nothing else about you is kept on the service.
- **Posts that already went out.** They are on Instagram, like any other post. Delete them in the Instagram app if you want them gone.

## Contact

If something is unclear, open an issue at <https://github.com/DoipSter/PostQueue/issues>.
