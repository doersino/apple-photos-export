# apple-photos-export

**Please note that `apple-photos-export.py` has been written to fit _my_ (admittedly weird) use case. No care was taken to make it particularly useful to anyone else. Perhaps most notably, it's *not an all-purpose backup tool* (I don't think one exists). Continue reading to find out what exactly it does.**

*But first, another bunch of disclaimers: All of the below potentially only works for whatever version of macOS/Photos was most recent at the time of the most recent commit to this repository. The code is somewhat convoluted and will invariably break once a major update comes along. It's only been tested for photos imported into an Apple Photos library via USB – I haven't yet tried how using iCloud changes things. Also, I don't use Photos for anything else and have never created an album. From the beginning, my iPhone was set to use the HEIC format, Live Photos have always been enabled and for HDR photos, the non-HDR variant is alst stored. Lastly, I've got an iPhone 7 – so there's no way for me to tell how Portrait mode photos are stored.*

---

An Apple Photos export script.

<center><img src="apple-photos-export.jpg" width="128"></center>


## Setup and usage

1. Install `exiftool` and `python3`.
2. Make sure `sips` is working (this should be included in your macOS installation).
3. `pip3 install configfile`.
4. Copy `apple-photos-export.ini.example` to `apple-photos-export.ini` in your desired target path (and fill in the details).

With this setup work out of the way, all that's left is to plug your iPhone into your MacBook, import all (or some) photos and run:

```sh
python3 apple-photos-export.py TARGET [-v]
```

This will read the config file, export all media [to a location within the depths of `/tmp` and only upon your confirmation copy them] to the `TARGET`, structured as shown below. Additionally, a cache file `TARGET/apple-photos-export.json` containing a record of already-exported photos and some metadata will be created.

```r
TARGET
└── apple-photos-export.ini

TODO
```


# The very interesting backstory

Back in the olden days, when I was using an Android-powered Nexus 5 and Dropbox's "Camera Uploads" feature, everything was great:

1. The phone would save photos (whether HDR or not) as `.jpg`, videos as `.mp4` and screenshots as `.png`.
2. Dropbox would continuously collect them and they'd end up on a folder on my laptop (with more or less sensible, time-based filenames), from where I could periodically archive them to a big external disk (and a backup, of course).

Then I got myself an iPhone, which – in addition to "normal" photos and videos – takes Live Photos, for which Dropbox only uploads the "base" photo. To make matters worse, some apps such as WhatsApp store received images in the camera roll, which messes everything up unless some filtering is done. In a futile attept to future-proof things (and for portability), I thought it'd be neat to generate a JPEG version of all HEIC files.

Wanting to keep my previous archival scheme running (and having it be complete, i.e. also containing the short videos corresponding to live photos), I've come up with the following workflow:

1. Connect the iPhone to my MacBook via USB.
2. Import all new photos into Photos.app.
3. ??? (this is where `apple-photos-export.py` comes in).
4. Success.


## Notes on `photos.db` and the directory structure of `~/Pictures/Apple Photos.photoslibrary`

*Current as of February 2019 (iOS 12.1.2, macOS 10.14.2 Mojave, Photos 4.0).*

In order to write `apple-photos-export.py`, I needed to reverse-engineer how Apple Photos stores and keep track of photos. Initially, this promised to be a piece of cake since Photos, inside the `Photos Library.photoslibrary`, uses an SQLite database `Photos Library.photoslibrary/database/photos.db` to keep track of the run-of-the-mill files it imports.

Upon further investigation, this proved a bit frustrating since the database really doesn't seem to contain much of the detail needed to get to the Live Photo videos and, to a lesser degree, discern different types of media. My solutions to these issues are encoded in `apple-photos-export.py`. The following SQL query gives sort of an overview:

```sql
SELECT modelId,               -- ID
       imagePath,             -- Absolute path to the base image.
       fileModificationDate,  -- Useful for matching slomo videos with rendered variants auto-generated by Photos.
       mediaGroupId,          -- Corresponds to the ContentIdentifier EXIF key in Live Photo videos, required for matching.
       burstUuid,             -- If set, we're dealing with a photo taken in burst mode (this allows grouping of bursts; also RKVersion contains a column burstPickType which I think indicates the best picture of a given burst).
       UTI,                   -- File type, commonly one of: public.heic, public.jpeg (WhatsApp/burst/panorama), com.apple.quicktime-movie, public.png, public.mpeg-4 (WhatsApp videos).
       importGroupUuid,       -- The import group (each time you import some pictures into Photos, an import group is created) the picture is part of. Allows trivially ignoring previously-exported imports for a significant speedup.
       hasAttachments         -- Indicates whether Photos has created a rendered slomo video or if you've performed any edits to the photo.
FROM RKMaster                 -- Most important table, also worth taking a look at: RKVersion, RKAttachment.
```

A commented tree view of the directory structure of `Photos Library.photoslibrary`:

```r
Photos Library.photoslibrary
├── Attachments/          # Not-really-useful metadata for adjustments.
│   └── ...
├── Masters/              # Master (original, un-edited) photos, organized in subdirectories according to import group dates.
│   ├── 2018/
│   │   └── 12/
│   │       └── 28/
│   │           └── 20181228-132551/
│   │               ├── IMG_0001.HEIC
│   │               ├── IMG_0002.HEIC
│   │               └── ...
│   └── 2019/
│       └── ...
├── database/             # Database and database-related files.
│   ├── photos.db
│   └── ...
├── private/
│   └── ...
└── resources/
    ├── media/
    │   ├── face/         # Extracted (and partially somewhat distorted) faces in image form (this directory might take a few days to populate).
    │   │   └── ...
    │   ├── master/       # Live Photo videos (among some other stuff, like JPEG versions of screenshots).
    │   │   ├── 00/
    │   │   │   └── 00/
    │   │   │       ├── fullsizeoutput_fe.jpeg
    │   │   │       ├── ...
    │   │   │       ├── jpegvideocomplement_1.mov
    │   │   │       ├── jpegvideocomplement_10.mov
    │   │   │       ├── jpegvideocomplement_11.mov
    │   │   │       ├── ...
    │   │   │       └── jpegvideocomplement_ff.mov
    │   │   └── ...
    │   ├── t/
    │   │   └── ...
    │   └── version/      # Rendered slomo videos and rendered versions of edited photos.
    │       ├── 00/
    │       │   └── 00/
    │       │       ├── fullsizeoutput_16.jpeg
    │       │       └── fullsizeoutput_22.jpeg
    │       ├── 03/
    │       │   └── 00/
    │       │       ├── fullsizeoutput_35d.mov
    │       │       ├── fullsizeoutput_363.mov
    │       │       └── fullsizeoutput_36b.mov
    │       └── 05/
    │           └── 00/
    │               ├── fullsizeoutput_521.jpeg
    │               └── videocomplementoutput_522.mov
    ├── moments/          # Some .plist files, nothing much useful.
    │   └── ...
    ├── projects/
    ├── proxies/
    │   └── derivatives/  # Thumbnails.
    │       └── ...
    ├── recovery/         # Databse backups in some weird format, I think.
    │   ├── Info.plist
    │   ├── RKAdjustmentData/
    │   │   └── 0000000000.lij
    │   └── ...
    └── segments/
        └── ...
```


## Future work

* [ ] iCloud support.
* [ ] Named faces support (don't yet use this feature; take a look at https://github.com/SummittDweller/merge-photos-faces/blob/master/main.py and https://github.com/patrikhson/photo-export/blob/master/photo.py).
* [ ] Portrait mode support (don't have the required hardware).
* [ ] Maybe export rendered variants of edited photos (take a look at https://github.com/orangeturtle739/photos-export)?
* [ ] ...
