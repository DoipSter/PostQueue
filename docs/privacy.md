---
title: Privacy policy
permalink: /privacy/
---

# PostQueue privacy policy

Last updated: 27 October 2026.

PostQueue is a desktop app for Windows and macOS. It posts your photos and videos to Instagram at the dates and times you choose. This page says what the app keeps, where it keeps it, and what it sends out.

The short version: everything stays on your own computer, except what Instagram needs to publish a post. PostQueue has no user accounts of its own, no analytics, no crash reports, no update checks and no ads. Nobody at PostQueue can see your queue, your captions or your photos.

## What stays on your computer

PostQueue keeps everything in one folder:

- Windows: `%LOCALAPPDATA%\PostQueue` (usually `C:\Users\<you>\AppData\Local\PostQueue`)
- macOS: `~/Library/Application Support/PostQueue`

The folder holds:

- **The queue** (`posts.json`): the date, time, caption and settings of each post you added.
- **Copies of your files** (`media` folder): a copy of each photo and video in the queue, so the originals can be moved or deleted while a post is waiting.
- **Settings** (`settings.json`): your preferences, the name and ID of the Instagram account you signed in with, the Facebook Page it is linked to, and the sign-in token described below.
- **A log** (`postqueue.log`): file names, times, what was posted, and error messages. It never contains the token.

### The sign-in token

When you sign in, Meta gives PostQueue a token: a long code that lets the app publish to your account. It is valid for about 60 days. PostQueue stores it on your computer only, protected so that other programs and other user accounts cannot read it:

- Windows: encrypted with Windows Data Protection (DPAPI), tied to your Windows user account.
- macOS: stored in your login keychain.

Pressing **Disconnect** in **Settings › Instagram** deletes the token. Removing PostQueue on Facebook or Instagram (see below) cancels it on Meta's side, so it stops working even if a copy remained.

## What is sent, and to whom

### Meta (Facebook and Instagram)

Signing in happens in your browser, on Meta's own pages. PostQueue never sees your password.

After that, PostQueue talks to Meta's Instagram Graph API on your behalf. It sends the token, the caption of each post and a link to the photo or video. It reads the name and ID of your Instagram account, the list of Facebook Pages you manage (to find the Instagram account linked to one of them), your remaining daily publishing quota, and the result of each post. Meta handles that data under its own policies: the [Meta Privacy Policy](https://www.facebook.com/privacy/policy/) and the [Instagram Data Policy](https://privacycenter.instagram.com/policy).

### The PostQueue service

This applies when you used the built-in sign-in (the three-screen setup). The PostQueue service is a small program hosted on Cloudflare. It does two things a desktop app cannot do on its own:

1. **It finishes the sign-in.** Meta's sign-in page hands the app a short-lived code. The service exchanges it for the 60-day token and returns the token to the app. The service keeps nothing: no code, no token, no record of who signed in.
2. **It hosts each photo or video for a moment.** Instagram's API cannot take a file upload; it needs a public link it can fetch. So, at posting time, PostQueue uploads the file to the service under a random, unguessable name, tells Instagram the link, and deletes the file right after the post is published. If the app could not delete it (for example because the computer was switched off mid-post), an hourly job deletes anything older than a day. So a file is on the service for a few seconds in the normal case, and for at most 24 hours in the worst case. While it is there, anyone who knows the exact link can fetch it, which is what lets Instagram fetch it.

Only people signed in to PostQueue can upload to or delete from the service. The service stores no captions, no account names and no list of users. It is hosted on Cloudflare (Workers and R2 storage); Cloudflare processes the connection itself, including your IP address, under the [Cloudflare privacy policy](https://www.cloudflare.com/privacypolicy/).

### Your own hosting (advanced setup only)

If you chose the advanced setup, with your own Meta app and your own Cloudinary account, PostQueue uploads each file to your Cloudinary account instead of the PostQueue service, and removes it after posting. That is covered by your own agreement with Cloudinary. The PostQueue service is not used at all in that case.

### Nothing else

That is the complete list. PostQueue does not send anything to anyone else, does not sell or share data, and does not build a profile of you.

## Your choices

- **Disconnect:** Settings › Instagram › Disconnect. The token is deleted from your computer.
- **Stop PostQueue's access at Meta:** on Facebook, Settings and privacy › Settings › Apps and websites › PostQueue › Remove. On Instagram, Settings › Website permissions › Apps and websites › PostQueue › Remove.
- **Delete everything:** see [Delete your data](data-deletion.md).

## Children

PostQueue is for Instagram professional accounts. It is not meant for anyone under 13, and PostQueue does not knowingly handle data about children.

## Changes to this policy

When this page changes, the date at the top changes with it. Earlier versions are in the project's history on GitHub.

## Contact

Questions about privacy: open an issue at <https://github.com/DoipSter/PostQueue/issues>.
