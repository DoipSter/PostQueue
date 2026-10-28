# PostQueue, picture by picture: from first launch to a posted photo

Every screen the user sees, in order, with what to do on each. The pictures are rendered from the real program by `tools\walkthrough.py` (with the built-in Meta app and service configured, so setup is the short path), and the red badges mark what to press.

## First launch

![First launch](01-first-launch.png)

On the first run the setup screens come first and the window itself appears only once they are finished, so there is nothing to click by mistake before then. Closing the setup early quits PostQueue; the next start begins with the setup again. The picture above shows the one case where the window appears with setup incomplete: if it ever becomes incomplete later (after signing out, say), a yellow bar says what is missing and **Finish setup** (1) reopens the screens.

## Setup: three screens

![Welcome](02-setup-welcome.png)

**1** Press **Start**.

![Professional account](03-setup-professional-account.png)

**1** Make sure the Instagram account is a professional one (Instagram app › Settings › Account type and tools › Switch to professional account), then press **Done, next**. The drawing of the phone is a demo that plays on its own: the pointer moves to each thing to tap, and the caption under it says which step it is on.

![Sign in](04-setup-sign-in.png)

**1** Press **Sign in in the browser**. The browser opens Meta's sign-in page.

![Waiting for the browser](05-setup-sign-in-waiting.png)

The screen waits while you approve PostQueue in the browser.

![Connected](06-setup-connected.png)

It says **Connected as @yourname** and moves on by itself.

![Posting time](07-setup-posting-time.png)

**1** Pick the time of day you usually post. **2** Press **Next**. Every photo you add later lands on the next free day at this time.

![All set](08-setup-done.png)

**1** Press **Start using PostQueue**. Setup is finished and never asked again.

## Posting

![Ready to add](09-ready-to-add.png)

**1** Press **Add photos or videos**, or **2** drop files onto the window.

![Photo added](10-photo-added.png)

The photo appears in the queue, already scheduled for the next free slot, and is selected on the right. **1** Click the date to change it. **2** Set the time. **3** Write the caption.

![Pick a date](11-pick-a-date.png)

The date button opens this calendar; click a day.

![Caption and save](12-caption-and-save.png)

**1** The caption, with hashtags and mentions; the counter under it warns when it is too long. **2** Press **Save changes**.

![Saved and scheduled](13-saved-and-scheduled.png)

**1** The status bar names the next post and when it goes out. From here, nothing more to do: close the window and PostQueue waits in the notification area (menu bar on a Mac).

![Posting](14-posting.png)

**1** At the scheduled minute, the progress bar shows each step: preparing the photo, uploading, creating the Instagram post, publishing.

## Confirmation

![Posted](15-posted-confirmation.png)

**1** A green strip confirms what was posted and when. **2** **Open on Instagram** shows the post in the browser. **3** The same button sits in the details pane, and the row reads **Posted**. The strip stays until you dismiss it; when the window was closed, the same message appears as a notification.

![Failed with reason](16-failed-with-reason.png)

**1** If a post could not go out, a red strip says which file and why, in plain words. **2** Fix the cause (here: sign in again in Settings), then select the row and press **Post now**, or change its time and save. Temporary problems such as a lost connection are retried three times by themselves before a post is marked failed.
