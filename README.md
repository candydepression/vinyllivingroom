# Vinyl Living Room

Turns a phone video plus the TP-7 recording of a whole album into a finished YouTube video, on your Mac.

- Lines up the phone video with the TP-7 audio automatically, including the tiny clock drift between the two devices. The TP-7 audio is the master and is never stretched.
- Keeps the entire session, silence and flip included.
- Opens with the title card (album, then artist, centred on black). The picture then fades up from black over 5 seconds and fades back to black over the last 5.
- Applies a gentle nostalgic colour look: warmer, softer blacks, a little less colour, fine grain.
- Sets the volume to a YouTube-friendly level with a plain volume change, never compression.
- Uploads to YouTube as **private**, and only when you click Upload and confirm.

## Setup (once)

1. Install Homebrew from https://brew.sh (paste the one line it shows into Terminal).
2. Get this folder onto your Mac, for example with GitHub Desktop or `git clone`.
3. In Terminal: `cd` into the folder, then run `./setup.sh`. This installs ffmpeg and the Python bits.

## Every week

1. Double-click **`Vinyl Living Room.command`**. A page opens in your browser.
2. Click **+ New episode**. Drop in the phone video and the TP-7 `.wav`, then type the artist, album, YouTube title, description and tags. Everything saves automatically.
3. Click **Preview**. In a minute or two you get the opening, a minute from the middle (to check lip-sync), the ending, and before/after colour shots.
4. Click **Make video** to render the full album. This takes a while; leave the window open.
5. Click **Show in Finder** and watch the finished file.
6. When you're happy, click **Upload to YouTube (private)** and confirm. Then publish it yourself in YouTube Studio.

Episodes live in `~/Movies/Vinyl Living Room/`, one folder each. Dropped files are **copied** there, so you can delete the originals afterwards if you need the space.

### Recording tips
- Keep the phone recording sound. The sync uses it.
- On an iPhone, turning off **Settings › Camera › Record Video › HDR Video** gives the most predictable colours. HDR video is converted automatically, but colours vary more.
- If the phone stops recording partway (storage full, a call), sync will fail rather than guess.

### Options
Under **More options** on the page:
- **Start / End** trims the beginning or end, in the phone video's own time as QuickTime shows it (e.g. `0:12`, `44:30`). Leave them empty to keep everything.
- **Colour look** runs from 0 (off) to 2 (strong). The default is 1.

Other defaults, such as fade lengths, title time, loudness and resolution, are in `vlr/episode.py` (`DEFAULT_SETTINGS`). Any of them can be overridden per episode in that episode's `episode.toml` under `[settings]`.

## Connect YouTube (one time)

The Upload button needs your own Google "app" key. It takes about 10 minutes:

1. Go to https://console.cloud.google.com, sign in with the account that owns the channel, and create a project (any name).
2. **APIs & Services › Library**: enable **YouTube Data API v3**.
3. **OAuth consent screen** (also called Google Auth Platform): choose **External**, fill in the app name and your email, and add your own Gmail address as a **test user**.
4. **Credentials › Create credentials › OAuth client ID › Desktop app**. Download the JSON file.
5. Rename it to `client_secret.json` and put it in `~/.config/vinyllivingroom/`. In Finder: Go › Go to Folder…, type `~/.config`, and create the `vinyllivingroom` folder if it isn't there.

The first upload opens a Google sign-in page in your browser.

**Known limits:**
- **Videos may stay locked private.** Google's own docs disagree on this point. Some versions say uploads from a personal, unaudited API project are locked to private until the project passes a YouTube API audit; another says they aren't. If YouTube won't let you make an uploaded video public, either apply for the audit (YouTube API Services audit form) or upload the finished file yourself by dragging it into YouTube Studio. The video file is the same either way.
- **You may need to sign in again every week.** While the Google app is in "testing" mode, Google may expire the sign-in after about 7 days.

## Command line (optional)

```
./vinyl app                       # the drop-in page
./vinyl new  ~/Movies/some-folder # make episode.toml for a folder with a video + a wav
./vinyl preview ~/Movies/some-folder
./vinyl make    ~/Movies/some-folder [--fast]   # --fast uses the Mac's hardware encoder
./vinyl upload  ~/Movies/some-folder
```

## How the sync works

The phone's microphone hears the same music the TP-7 records. The tool compares the two recordings at a checkpoint every ~20 seconds, then fits a straight line through the measurements:

    phone time = TP-7 time × (1 + drift) + offset

The video's timing is then remapped onto the TP-7 timeline. The audio stays bit-for-bit the TP-7's, apart from the volume change and short fades. The flip and other silent stretches are simply skipped when measuring. `out/report.txt` records the offset, the drift in parts per million, and the fit error in milliseconds. Anything above 15 ms stops with an error instead of producing a video that's out of sync.

## Tests

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests build fake sessions with a known offset and an exaggerated clock drift. They check that the finished video's flashes land on the audio clicks, from the start to the end of the video.

## Fonts

The title uses Jost Light (`fonts/Jost.ttf`, SIL Open Font License, see `fonts/OFL.txt`).
