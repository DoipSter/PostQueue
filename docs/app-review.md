---
title: Meta App Review notes
permalink: /app-review/
---

# Meta App Review: submission notes for PostQueue

The built-in sign-in (the three-screen setup) uses one Meta app that ships inside PostQueue. While that app is in development mode, only its listed testers can sign in. For everyone else, Meta has to approve the app: that is **App Review**, and the publishing permission also needs **business verification**. This page is everything needed for the submission, in the order Meta asks for it. `BUILD_AND_TEST_GUIDE.md` section 7 describes the app and the service it depends on.

Public addresses used below (GitHub Pages from the `docs` folder):

| What Meta asks for | Address |
|---|---|
| Privacy Policy URL | <https://doipster.github.io/PostQueue/privacy/> |
| User data deletion (instructions URL) | <https://doipster.github.io/PostQueue/data-deletion/> |
| Terms of Service URL (optional) | leave empty, or point at the repository's MIT licence: <https://github.com/DoipSter/PostQueue/blob/main/LICENSE> |

## 1. Before submitting

Do these in the [App Dashboard](https://developers.facebook.com/apps/) first. Review requests with gaps here get sent back without being read.

- [ ] **App settings › Basic:** display name `PostQueue`, an app icon (1024 × 1024 PNG; the logo in `make_assets.py` scales up), category `Business and pages`, a contact email you read, the privacy policy URL and the data deletion URL above. Save.
- [ ] **Facebook Login › Settings:** `http://localhost:48215/` under Valid OAuth Redirect URIs. Client OAuth login and Web OAuth login on; everything else off.
- [ ] **Products:** Facebook Login and Instagram are added. If the "Instagram API with Instagram Login" product is added too, its two permissions (`instagram_business_basic`, `instagram_business_content_publish`) need their own review entries; the paragraphs below apply word for word, only the names differ.
- [ ] **Business portfolio:** the app is connected to a business portfolio (App settings › Basic › Business verification). Create one in [Meta Business Suite](https://business.facebook.com/) if there is none. Business verification (section 5) runs against this portfolio.
- [ ] **The scopes the app requests match the submission.** The sign-in asks for exactly the permissions being reviewed. In Meta's live mode, asking for a permission that was not approved makes the sign-in dialog fail with "Invalid Scopes". Today `FB_SCOPES` in `instagram.py` also lists `business_management`, which is not needed for the Page lookup (`pages_show_list` covers `me/accounts`) and is not in this submission. Remove it from `FB_SCOPES` before the build that goes public, or add it to the review with a matching justification.
- [ ] **A test account for the reviewer:** an Instagram professional account (Business or Creator) linked to a Facebook Page, which you control and which holds nothing private. Add it as an Instagram Tester under App roles › Roles and accept the invitation in the Instagram app (Settings › Website permissions › Apps and websites › Tester invites). Its sign-in details go into the submission's test instructions, nowhere else.
- [ ] **A download the reviewer can run:** a GitHub release with the Windows MSI and the macOS DMG built with the same App ID (`branding.py`, or the CI variables). Reviewers cannot build from source.
- [ ] **The screen recording** (section 3), as an MP4 under 100 MB.

## 2. Permissions and what each is for

Request **Advanced access** for all four. In the dashboard they sit under App Review › Permissions and features, or, in dashboards that show **Use cases**, under the use case's Customize page; both lead to the same form. Each permission has a "detailed description" box; the paragraph for it below is written to be pasted there. Meta's reviewers check that the description, the recording and what the app does all match.

### instagram_basic

PostQueue uses `instagram_basic` to identify the Instagram account the user signed in with and to show it back to them. Right after sign-in, the app reads the account's ID and username (through the Facebook Page's `instagram_business_account` field) and displays "Connected as @username" so the user can confirm they are posting to the right account. The ID is then used as the target of every publish call. After a post is published, the app reads the new post's permalink to show an "Open on Instagram" button in its confirmation strip. No other profile data, media list or insights are read, and nothing is stored anywhere except on the user's own computer.

### instagram_content_publish

PostQueue uses `instagram_content_publish` to publish the user's own photos and videos to their own Instagram professional account at the time they scheduled, which is the whole purpose of the app. At the scheduled minute, the app creates a media container (`/{ig-user-id}/media`) with a link to the photo or video and the caption the user wrote, polls the container's status, and publishes it (`/{ig-user-id}/media_publish`). Videos are published as Reels. The app also reads `content_publishing_limit` to warn the user before they hit the daily quota. Every post is created by the user, one at a time, in the app's window; there is no bulk posting, no reposting of other people's content and no automated content generation.

### pages_show_list

PostQueue uses `pages_show_list` once, at sign-in, to find the Instagram professional account linked to one of the user's Facebook Pages. The app calls `me/accounts` with the fields `name` and `instagram_business_account{id,username}`. If exactly one Page has an Instagram account, it is selected automatically; if several do, the app shows a list of Page names and the user picks one. The Page list is not stored; only the chosen Page's ID and the Instagram account's ID and username are saved on the user's computer.

### pages_read_engagement

PostQueue uses `pages_read_engagement` only because the Instagram Graph API requires it, together with `pages_show_list`, for the `me/accounts` call that returns the Page's name and its linked `instagram_business_account`. Without it the Page lookup returns no Instagram account and sign-in cannot complete. The app does not read Page posts, comments, insights or any engagement data, and it never writes to the Page.

## 3. The screen recording

One recording, two to four minutes, showing a real sign-in and a real post from start to finish. Meta wants to see the login dialog, every permission being used, and the result on Instagram. Record on the test account, with nothing personal on screen, at the screen's normal size (no window smaller than 1280 × 720). Windows: Win+Alt+R (Game Bar) records the active window. macOS: QuickTime Player › File › New Screen Recording. Export as MP4.

Before pressing record: install PostQueue fresh (or delete the data folder, see the deletion page), close the browser's Meta tabs, open the Instagram app on a phone next to the computer or the Instagram website in a second browser window.

| Step | What to do | What must be visible |
|---|---|---|
| 1 | Start PostQueue for the first time. | The window with the yellow setup bar, then the Welcome screen. Press **Start**. |
| 2 | Professional account screen: press **Done, next**. | The text explaining the account must be a professional one. |
| 3 | Sign in screen: press **Sign in in the browser**. | The browser opens Meta's login dialog with the app name **PostQueue** and the list of permissions. Pause there for two seconds so the reviewer can read it (`pages_show_list`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`). Approve. |
| 4 | Come back to PostQueue. | "Connected as @testaccount" (`instagram_basic`), and, if the account has several Pages, the Page picker (`pages_show_list`). |
| 5 | Posting time screen: pick a time, press **Next**, then **Start using PostQueue**. | The All set screen. |
| 6 | Add a photo: press **Add photos or videos…** or drop a JPG into the window. | The photo in the queue with the next free slot. |
| 7 | Press the date button, pick today in the calendar, set the time two minutes from now, type a caption, press **Save changes**. | The calendar, the time, the caption and the "Scheduled" state. |
| 8 | Wait. Keep the window on screen. | The status changing to **Posting…** and then the green strip "Posted … Open on Instagram" (`instagram_content_publish`). |
| 9 | Press **Open on Instagram**, or switch to the phone. | The same photo and caption on the test account's Instagram profile. |
| 10 | Back in PostQueue: Settings › Instagram, press **Disconnect**. | The token being removed. Optional, but it shows the user can disconnect. |

Narration is not needed; a short on-screen note in the submission's text is enough. The pictures in `docs/walkthrough` show each screen as it should look.

In the review form, next to the recording, paste the **test instructions**:

> PostQueue is a Windows and macOS desktop app. Download the installer from <release link>, install it (one click), start it and press Start. On the sign-in screen press "Sign in in the browser" and sign in with the test account below. Add any JPG, set its time to a minute or two from now, press Save changes, and wait: the post appears on the test account. Sign-in details: <test account email and password>.

## 4. Submitting

1. App Review › Requests (or the use case page) › add the four permissions › **Request advanced access**.
2. Fill in each permission's detailed description from section 2 and upload the recording once; it can be attached to all four.
3. Complete the **data handling questions** (Data Use Checkup): no data is shared with third parties, no data is used for anything but the purpose shown, data is stored on the user's device only. The privacy policy backs that up.
4. Confirm the **Platform Terms and Developer Policies** when asked.
5. Submit. Meta answers within about five working days. A rejection names the permission and the reason; most are "could not reproduce" (fix the test instructions) or "video does not show the permission in use" (re-record the step named).
6. After approval, switch the app to **Live** mode (the toggle at the top of the dashboard). From then on anyone can sign in. The scopes requested by the build must match what was approved (section 1, fifth item).

## 5. Business verification

Required for `instagram_content_publish` (and any other permission with advanced access for a Business-type app). It is done once per business portfolio, in Meta Business Suite › Settings › Security Center › **Start verification**, and takes between a day and two weeks.

- [ ] A business portfolio connected to the app (section 1).
- [ ] The business's legal name, address, phone number and website, exactly as they appear on the documents below. A sole trader can verify with their own name and address.
- [ ] One document that shows the legal name and address, in a format Meta lists for the country: business registration or incorporation certificate, tax registration, a utility bill or a bank statement less than a year old. PDF or a clear photo.
- [ ] A way for Meta to confirm the phone number or email: a code by SMS, call or email to a business address, or domain verification (a TXT record or an HTML file on the website).
- [ ] The portfolio's admin is the person doing the verification, with two-factor authentication on their Facebook account.
- [ ] The app's contact email in App settings › Basic is on the same domain as the business website, when there is one. It is not required, but it avoids a round of questions.

If PostQueue is published by an individual rather than a company, Meta's "individual verification" is the equivalent path: a government ID and a proof of address instead of business documents. It shows the same **Start verification** button.

## 6. What changes for users after approval

Nothing in the app. The sign-in screen stops saying that the app is in development mode, and anyone with a professional account can press Sign in. The tester list in the dashboard can stay as it is. Tokens issued before approval keep working.
