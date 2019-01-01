import os
import shutil
import sqlite3
import pyexiftool.exiftool as exif

#########
# USAGE #
#########

# * Install exiftool.

# Note: Only tested with photos imported from an iPhone via USB. No idea if, and
# how, this needs to be adjusted for iCloud use.

##########
# CONFIG #
##########

# Absolute path to the .photoslibrary package.
LIBRARY = "/Users/noah/Pictures/Photos Library 2.photoslibrary"

# Temporary storage.
TMP = "/Users/noah/Desktop/test_tmp"

# Target folder.
TARGET = "/Users/noah/Desktop/test_target"

################################################################################

# Path to "photos.db".
DATABASE = os.path.join(LIBRARY, "database/photos.db")
TMP_DB   = os.path.join(TMP, "photos.db")

# "Raw" images and videos, e.g. IMG_0042.{HEIC,MOV,PNG,JPG} (photos, vids,
# screenshots, bursts respectively). Images received by WhatsApp or exported
# from Dropbox etc. also show up here, but don't follow the "IMG_XXXX" naming
# scheme.
# For each import, a separate folder "YYYY/MM/DD/timestamp" exists.
MASTERS = os.path.join(LIBRARY, "Masters")

# Videos associated with live photos (possibly among other things), joinable
# through "Content Identifier" EXIF property.
# Subfolders.
LIVE_PHOTO_VIDEOS = os.path.join(LIBRARY, "resources/media/master")

# Slomo videos actually rendered as slomos etc. (And more?)
# Subfolders.
version  = os.path.join(LIBRARY, "resources/media/version")

def create_working_copy_of_photos_db():
    os.makedirs(TMP, exist_ok=True)
    shutil.copyfile(DATABASE, TMP_DB)

def get_masters():
    db_connection = sqlite3.connect(TMP_DB)
    db_cursor = db_connection.cursor()
    query = """
        SELECT modelId AS id,
               '{}' || '/' || imagePath AS absolutepath,
               fileCreationDate AS creationdate,
               mediaGroupId AS contentidentifier,  -- if set, need to get live photo video
               burstUuid AS burstid,  -- if set, could group burst mode pics
               UTI AS type,  -- public.heic, public.jpeg (whatsapp/burst/pano), com.apple.quicktime-movie, public.png, public.mpeg-4 (whatsapp movies)
               importGroupUuid AS importid,
               hasAttachments AS isslomoorhasjpegscreenshot
        FROM RKMaster
    """.format(MASTERS)
    db_cursor.execute(query)
    return list(db_cursor)

def analyze_masters():
    pass

def get_matching_live_photo_videos(live_photos):
    # TODO need to get the videos from the file system based on contentidentifier
    pass

def get_matching_slomos(videos):
    # TODO iff hasAttachments = 1? this also catches jpeg screenshots, good
    # join with RKAttachment table but that doesn't help much? reference to AEE/plist files that contain <string>com.apple.video.slomo</string> but nothing more; could try matching based on IMG_... "Creation Date" == fullsizeoutput... "Date Time Original" EXIF data
    # select * from RKMaster where fileName = 'IMG_0027.MOV';
    # 26|KnsExJJpSZeaq84eAnGU+g|AeIEW9HoEl03RoFYWMwASoRoBIfF|1|IMG_0027|567696357.092434|0||0|0|0|0|0|1.72166666666667|559410555|||4964099|720|1280|com.apple.quicktime-movie|EntveYbqTVaNWcxAB%yk+A||IMG_0027|IMG_0027.MOV|0|0|1|0|2018/12/28/20181228-132551/IMG_0027.MOV|559410555|559410555|IMG_0027.MOV|4964099|3|||1|7200|||1||
    pass

def main():
    create_working_copy_of_photos_db()
    print(get_masters())

    files = [MASTERS + "/2018/12/28/20181228-132551/IMG_0037.HEIC", "/Users/noah/Pictures/Photos Library 2.photoslibrary/resources/media/master/00/00/jpegvideocomplement_1.mov"]
    #print(files)
    with exif.ExifTool() as et:
        metadata = et.get_metadata_batch(files)
    for d in metadata:
        #print(d)
        try:
            print(d["MakerNotes:ContentIdentifier"])
            #print(d["QuickTime:ContentIdentifier"])
        except KeyError:
            print(d["QuickTime:ContentIdentifier"])
            pass

if __name__ == "__main__":
    main()

# glob images, go though them, collect content identifiers
# glob live photo videos, also collect content identifiers
# foreach image, try to find match
# if found, copy to output/date(formatted like dropbox camera uploads)_contentid_originalname
# TODO what to do about screenshots, bursts, whatsapp pics, other random pics, vids, non-live photos etc.?
# TODO prefix with type after date???

# TODO then what to do about pictures left on phone? delete all non-whatsapp ones? all IMG_xxx then, but how?

# https://github.com/xgess/timestamp_all_photos/blob/master/app/apple_photos_library.py
# https://github.com/SummittDweller/merge-photos-faces/blob/master/main.py
