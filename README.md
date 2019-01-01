# apple-photos-export

Back when I was using an Android-powered Nexus 5 and Dropbox's "Camera Uploads" feature, everything was great:

1. The phone would save photos (whether HDR or not) as `.jpg`, videos as `.mp4` and screenshots as `.png`.
2. Dropbox would continuously collect them and they'd end up on a folder on my laptop (with more or less sensible, time-based filenames), from where I could periodically archive them to a big external disk.

Then I got myself an iPhone, which – in addition to "normal" photos and videos – takes live photos, HDR photos etc., for which Dropbox only uploads the "base" photo. Also, some apps such as WhatsApp store received images in the camera roll, which messes everything up. And I also wanted a JPEG version of all HEIC files for future proofing slash portability.

Wanting to keep my previous archival scheme running (and having it be complete, i.e. also containing the short videos corresponding to live photos), I've come up with the following workflow:

1. Connect the iPhone to my laptop.
2. Import all new photos into Photos.app.
3. Magic (i.e. run the code contained in this repository, which is convoluted and will invariably break once a major update comes along).
4. Success.
5. TODO Delete everything from the phone and photos.app?


## Talk is cheap. Show me (how to use) the code.

1. Install `exiftool`.
2. Install `python3`.

TODO

Note: Only tested with photos imported from an iPhone via USB. No idea if, and how, this needs to be adjusted for iCloud use.


## Notes on `photos.db` and the folder structure of `.photoslibrary`

TODO


## Future work

* Faces.
