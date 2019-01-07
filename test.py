import os
import sys
import glob
import shutil
import sqlite3
import subprocess
import pyexiftool.exiftool as exif

from datetime import datetime

# TODO cleanup imports, really only import required functions

######################
# USAGE & BACKGROUND #
######################

# See README.md.

##########
# CONFIG #
##########

# Absolute path to the .photoslibrary package.
LIBRARY = "/Users/noah/Pictures/Photos Library 2.photoslibrary"

# Temporary storage.
TMP = "/tmp/apple-photos-export"

# Target folder.
TARGET = "/Users/noah/Desktop/photos_phone"

# Output verbosity.
VERBOSE = True  # TODO respect this parameter

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
#VERSION  = os.path.join(LIBRARY, "resources/media/version")

# TODO use os.path.join throughout, especially in assemble_prefix thingy

################################################################################

# general SELECT statement

# SELECT modelId AS id,
#     imagePath AS absolutepath,
#     fileCreationDate AS creationdate,
#     mediaGroupId AS contentidentifier,  -- if set, need to get live photo video
#     burstUuid AS burstid,               -- if set, could group burst mode pics
#     UTI AS type,                        -- public.heic, public.jpeg (whatsapp/burst/pano), com.apple.quicktime-movie, public.png, public.mpeg-4 (whatsapp movies)
#     importGroupUuid AS importid,
#     hasAttachments AS isslomoorhasjpegscreenshot
# FROM RKMaster

# WHERE predicates for known media types, where m is RKMaster

IS_PHOTO = """
WHERE m.mediaGroupId IS NOT NULL
AND m.UTI = 'public.heic'
"""

IS_VIDEO = """
WHERE UTI = 'com.apple.quicktime-movie'
"""

IS_BURST = """
WHERE m.UTI = 'public.jpeg'
AND m.burstUuid IS NOT NULL
"""

IS_PANORAMA = """
WHERE m.UTI = 'public.heic'
AND m.mediaGroupId IS NULL
"""

IS_INSTA = """
WHERE m.mediaGroupId IS NOT NULL
AND m.UTI = 'public.jpeg'
"""

IS_SCREENSHOT = """
WHERE m.UTI = 'public.png'
AND m.filename LIKE 'IMG_%'
"""

IS_WHATSAPP_PHOTO = """
WHERE m.UTI = 'public.jpeg'
AND m.burstUuid IS NULL
AND m.mediaGroupId IS NULL
AND substr(m.filename, 9, 1) = '-'
AND substr(m.filename, 14, 1) = '-'
AND substr(m.filename, 19, 1) = '-'
AND substr(m.filename, 24, 1) = '-'
AND length(m.filename) = 40
"""

IS_WHATSAPP_VIDEO = """
WHERE m.UTI = 'public.mpeg-4'
AND substr(m.filename, 9, 1) = '-'
AND substr(m.filename, 14, 1) = '-'
AND substr(m.filename, 19, 1) = '-'
AND substr(m.filename, 24, 1) = '-'
AND length(m.filename) = 40
"""

################################################################################

# as a sanity check, keep track of number of photos processed
TALLY = {}
def tally(category):
    global TALLY
    if category not in TALLY.keys():
        TALLY[category] = 1
    else:
        TALLY[category] = TALLY[category] + 1

def stats():
    # TODO make prettier
    print(TALLY)
    print(query("SELECT COUNT(*) FROM RKMaster"))

# query helper
def query(q):
    conn = sqlite3.connect(TMP_DB)
    c = conn.cursor()
    c.execute(q)
    res = list(c)
    conn.close()
    return res

def log(msg, type='status'):
    print(" " * 80, end="\r")
    if type == "status":
        print('\033[1m' + msg + '\033[0m')
    if type == "info":
        print(msg)
    if type == "warn":
        print('\033[38;5;208m' + "⚠️  " + msg + '\033[0m')
    if type == "error":
        sys.exit('\033[31m' + "❌  " + msg + '\033[0m')

# based on https://gist.github.com/vladignatyev/06860ec2040cb497f0f3
# TODO add proper license etc.
def progress(count, total, status=''):
    bar_len = 60
    filled_len = int(round(bar_len * count / float(total)))

    bar = '=' * filled_len + '-' * (bar_len - filled_len)
    percents = round(100.0 * count / float(total), 1)
    items = str(count) + "/" + str(total)

    sys.stdout.write('[%s] %s%s (%s)' % (bar, percents, '%', items))
    if status:
        sys.stdout.write(' -> %s' % (status))
    sys.stdout.write('\r')
    sys.stdout.flush()

    if count == total:
        sys.stdout.write('\n')
        sys.stdout.flush()

def jpeg_from_heic(heicfile, jpegfile):  # TODO setting for quality?
    try:
        subprocess.check_output(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", heicfile, "--out", jpegfile])
    except subprocess.CalledProcessError as err:
        log("sips failed: " + repr(err), "error")

def assemble_file_name_prefix(creationdate, id):
    ts = creationdate + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason
    # TODO maybe convert to system time zone? or "taken" time zone? this info can be found in the versions table
    datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    datestring = datestring.replace(" ", "_").replace(":", "-")
    yearmonth = datetime.utcfromtimestamp(ts).strftime('%Y/%m_%B')
    directory = TARGET + "/" + yearmonth
    os.makedirs(directory, exist_ok=True)
    filename_prefix = directory + "/" + datestring + "_" + str(id) + "_"
    return filename_prefix

def create_working_copy_of_photos_db():
    os.makedirs(TMP, exist_ok=True)
    shutil.copyfile(DATABASE, TMP_DB)

def init_target():
    os.makedirs(TARGET, exist_ok=True)

def collect_and_convert_photos():
    log("Querying database for photos and live photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               m.mediaGroupId AS contentidentifier,
               v.selfPortrait AS selfie
        FROM RKMaster m LEFT JOIN RKVersion v ON m.uuid = v.masterUuid
    """ + IS_PHOTO
    photos = query(q)

    log("Building index of live photo videos...")

    live_photo_videos = {}

    mov_files = glob.iglob(LIVE_PHOTO_VIDEOS + '/**/*.mov', recursive=True)
    with exif.ExifTool() as et:
        log("Batch-extracting metadata (this might take a minute or three)...", "info")
        metadata = et.get_metadata_batch(mov_files)

    log("Looking for QuickTime:ContentIdentifier fields...", "info")
    for d in metadata:
        try:
            live_photo_videos[d["QuickTime:ContentIdentifier"]] = d["SourceFile"]
        except KeyError:
            log("Couldn't find QuickTime:ContentIdentifier field for " + d["System:FileName"] + ", will ignore", "warn")

    log("Matching live photos with corresponding video files...")
    photos2 = [] # id, date, photo file, video file
    for l in photos:
        id = l[0]
        photopath = MASTERS + "/" + l[1]
        creationdate = l[2]
        contentidentifier = l[3]
        selfie = bool(l[4])
        try:
            videopath = live_photo_videos[contentidentifier]
            photos2.append(tuple([id, photopath, creationdate, videopath, selfie]))
        except KeyError:
            log("Couldn't find video file for " + photopath + ", will keep it without a video", "warn")
            photos2.append(tuple([id, photopath, creationdate, None, selfie]))

    log("Collecting photos and corresponding live photo video files and creating JPEG versions...")
    progress(0, len(photos2))
    for i, (id, photopath, creationdate, videopath, selfie) in enumerate(photos2):

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id)
        if selfie:
            filename_prefix = filename_prefix + "selfie_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photopath))
        targetphotopath = filename_prefix + pre + ext.lower()
        shutil.copyfile(photopath, targetphotopath)
        tally("livephoto")  # TODO isn't this really just HEIC photos? i.e. shouldn't this entire thing be renamed?

        # create jpeg version
        targetjpegpath = filename_prefix + pre + ".jpg"
        jpeg_from_heic(photopath, targetjpegpath)
        tally("livephotojpeg")

        # copy live video if it exists
        if videopath is not None:
            targetvideopath = filename_prefix + os.path.basename(videopath)
            shutil.copyfile(videopath, targetvideopath)
            tally("livephotovideo")

        tally("total")
        progress(i+1, len(photos2), os.path.basename(photopath))

    # hdr images are already "baked in", the non-hdr version seems not to be gettable

def collect_videos():
    log("Querying database for videos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               a.filePath AS attachment
        FROM RKMaster m LEFT JOIN RKAttachment a on m.uuid = a.attachedToUuid
    """ + IS_VIDEO
    videos = query(q)

    log("Collecting videos and real-time, high-framerate versions of slomos...")
    progress(0, len(videos))
    for i, l in enumerate(videos):
        id = l[0]
        videopath = MASTERS + "/" + l[1]
        creationdate = l[2]
        attachment = l[3]

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id)
        if attachment:
            filename_prefix = filename_prefix + "slomo_"

        # copy video
        pre, ext = os.path.splitext(os.path.basename(videopath))
        targetvideopath = filename_prefix + pre + ext.lower()
        shutil.copyfile(videopath, targetvideopath)
        tally("videos")

        tally("total")
        progress(i+1, len(videos), os.path.basename(videopath))

def get_matching_slomos(videos):

    # TODO rendered versions of slomos (see resources/media/version)
    # iff hasAttachments = 1? this also catches jpeg screenshots, good
    # join with RKAttachment table but that doesn't help much? reference to AEE/plist files that contain <string>com.apple.video.slomo</string> but nothing more; could try matching based on IMG_... "Creation Date" == fullsizeoutput... "Date Time Original" EXIF data
    # select * from RKMaster where fileName = 'IMG_0027.MOV';
    # 26|KnsExJJpSZeaq84eAnGU+g|AeIEW9HoEl03RoFYWMwASoRoBIfF|1|IMG_0027|567696357.092434|0||0|0|0|0|0|1.72166666666667|559410555|||4964099|720|1280|com.apple.quicktime-movie|EntveYbqTVaNWcxAB%yk+A||IMG_0027|IMG_0027.MOV|0|0|1|0|2018/12/28/20181228-132551/IMG_0027.MOV|559410555|559410555|IMG_0027.MOV|4964099|3|||1|7200|||1||

    pass

def collect_bursts():
    log("Querying database for burst photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               m.burstUuid AS burstid
        FROM RKMaster m
    """ + IS_BURST
    bursts = query(q)

    # TODO RKVersion contains column burstPickType indicating (?) which image was chosen as the "hero" image

    log("Collecting burst photos...")
    progress(0, len(bursts))
    for i, l in enumerate(bursts):
        id = l[0]
        burstpath = MASTERS + "/" + l[1]
        creationdate = l[2]
        burstid = l[3]

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id) + "burst_" + burstid + "_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(burstpath))
        targetburstpath = filename_prefix + pre + ext.lower()
        shutil.copyfile(burstpath, targetburstpath)
        tally("bursts")

        tally("total")
        progress(i+1, len(bursts), os.path.basename(burstpath))

def collect_panoramas():
    log("Querying database for panoramas...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + IS_PANORAMA
    panoramas = query(q)

    log("Collecting panoramas and creating JPEG versions...")
    progress(0, len(panoramas))
    for i, l in enumerate(panoramas):
        id = l[0]
        panoramapath = MASTERS + "/" + l[1]
        creationdate = l[2]

        # assemble filename prefix  # TODO abstract this
        filename_prefix = assemble_file_name_prefix(creationdate, id) + "panorama_"

        # copy photo  # TODO abstract
        pre, ext = os.path.splitext(os.path.basename(panoramapath))
        targetpanoramapath = filename_prefix + pre + ext.lower()
        shutil.copyfile(panoramapath, targetpanoramapath)
        tally("panoramas")

        # create jpeg version
        targetjpegpath = filename_prefix + pre + ".jpg"
        jpeg_from_heic(panoramapath, targetjpegpath)
        tally("panoramajpeg")

        tally("total")
        progress(i+1, len(panoramas), os.path.basename(panoramapath))

def collect_insta_photos():
    log("Querying database for Instagram photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + IS_INSTA
    instas = query(q)

    log("Collecting Instagram photos...")
    progress(0, len(instas))
    for i, l in enumerate(instas):
        id = l[0]
        instapath = MASTERS + "/" + l[1]
        creationdate = l[2]

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id) + "instagram_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(instapath))
        targetinstapath = filename_prefix + pre + ext.lower()
        shutil.copyfile(instapath, targetinstapath)
        tally("insta")

        tally("total")
        progress(i+1, len(instas), os.path.basename(instapath))

        # TODO figure out how to get actual date? is that even possible? => from original file creation/edit date

def tally_other_known_media():
    log("Querying database for other known kinds of images...")

    log("Tallying Screenshots...", "info")
    q = "SELECT 1 FROM RKMaster m" + IS_SCREENSHOT
    screenshots = query(q)
    for l in screenshots:
        tally("screenshot")
        tally("total")

    log("Tallying WhatsApp images...", "info")
    q = "SELECT 1 FROM RKMaster m" + IS_WHATSAPP_PHOTO
    whatsapp_images = query(q)
    for l in whatsapp_images:
        tally("whatsapp_image")
        tally("total")

    log("Tallying WhatsApp videos...", "info")
    q = "SELECT 1 FROM RKMaster m" + IS_WHATSAPP_VIDEO
    whatsapp_videos = query(q)
    for l in whatsapp_videos:
        tally("whatsapp_video")
        tally("total")

def main():
    create_working_copy_of_photos_db()

    init_target()

    # TODO store most recent import uuid or a set of all of them in metadata
    # file (read in init_target()), then proceed based on that (only export newer
    # photos) if it‘s present. also keep cache of video metadata in there maybe?
    # TODO "that means you've run this thingy most recently between x and y (can
    # get this info based on grouping by import id and getting min/mix timestamp
    # for the newest known and oldest unknown). correct?"

    collect_and_convert_photos()
    collect_videos()
    collect_bursts()
    collect_panoramas()
    collect_insta_photos()

    tally_other_known_media()

    stats()

if __name__ == "__main__":
    main()

# TODO what to do about screenshots, whatsapp pics, other random pics, non-live photos etc.?

# TODO then what to do about pictures left on phone? delete all non-whatsapp ones? all IMG_xxx then, but how?
# TODO how to deal with multiple imports? add -import parameter that adds the corresponding predicate (master table has import id column) to the queries?

# https://github.com/xgess/timestamp_all_photos/blob/master/app/apple_photos_library.py
# https://github.com/SummittDweller/merge-photos-faces/blob/master/main.py
