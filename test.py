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
VERSION  = os.path.join(LIBRARY, "resources/media/version")

# TODO use os.path.join throughout, especially in assemble_prefix thingy

################################################################################

# SELECT modelId AS id,
#     imagePath AS absolutepath,
#     fileCreationDate AS creationdate,
#     mediaGroupId AS contentidentifier,  -- if set, need to get live photo video
#     burstUuid AS burstid,               -- if set, could group burst mode pics
#     UTI AS type,                        -- public.heic, public.jpeg (whatsapp/burst/pano), com.apple.quicktime-movie, public.png, public.mpeg-4 (whatsapp movies)
#     importGroupUuid AS importid,
#     hasAttachments AS isslomoorhasjpegscreenshot
# FROM RKMaster



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

def get_and_collect_and_convert_live_photos():
    log("Querying database for live photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               m.mediaGroupId AS contentidentifier,
               v.selfPortrait AS selfie
        FROM RKMaster m LEFT JOIN RKVersion v ON m.uuid = v.masterUuid
        WHERE m.mediaGroupId IS NOT NULL
        AND m.UTI = 'public.heic';
    """
    lives = query(q)

    log("Building index of live photo videos...")

    vids = {}

    mov_files = glob.iglob(LIVE_PHOTO_VIDEOS + '/**/*.mov', recursive=True)
    with exif.ExifTool() as et:
        log("Batch-extracting metadata (this might take a minute or three)...", "info")
        metadata = et.get_metadata_batch(mov_files)

    log("Looking for QuickTime:ContentIdentifier fields...", "info")
    for i, d in enumerate(metadata):
        try:
            #print(d["QuickTime:ContentIdentifier"])
            vids[d["QuickTime:ContentIdentifier"]] = d["SourceFile"]
        except KeyError:
            log("Couldn't find QuickTime:ContentIdentifier field for " + d["System:FileName"] + ", will ignore", "warn")

    log("Matching live photos with corresponding video files...")
    live_idpvs = [] # id, date, photo file, video file
    for l in lives:
        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]
        contentidentifier = l[3]
        selfie = bool(l[4])
        #print(selfie)
        #print(absolutepath)
        try:
            videopath = vids[contentidentifier]
            #print(videopath)
            live_idpvs.append(tuple([id, creationdate, absolutepath, videopath, selfie]))
        except KeyError:
            log("Couldn't find video file for " + absolutepath + ", will keep it without a video", "warn")
            live_idpvs.append(tuple([id, creationdate, absolutepath, None, selfie]))

    log("Collecting live photos and corresponding video files and creating JPEG versions...")
    progress(0, len(live_idpvs))
    for i, (id, creationdate, photo, video, selfie) in enumerate(live_idpvs):

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id)
        if selfie:
            filename_prefix = filename_prefix + "selfie_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = filename_prefix + pre + ext.lower()
        #print(targetphoto)
        shutil.copyfile(photo, targetphoto)
        tally("livephoto")  # TODO isn't this really just HEIC photos? i.e. shouldn't this entire thing be renamed?

        # create jpeg version
        targetjpeg = filename_prefix + pre + ".jpg"
        jpeg_from_heic(photo, targetjpeg)
        tally("livephotojpeg")

        # copy live video if it exists
        if video is not None:
            targetvideo = filename_prefix + os.path.basename(video)
            #print(targetvideo)
            shutil.copyfile(video, targetvideo)
            tally("livephotovideo")

        tally("total")
        progress(i+1, len(live_idpvs), os.path.basename(photo))

def get_and_collect_insta_photos():
    log("Querying database for Instagram photos...")

    q = """
        SELECT modelId AS id,
               imagePath AS absolutepath,
               fileCreationDate AS creationdate
        FROM RKMaster
        WHERE mediaGroupId IS NOT NULL
        AND UTI = 'public.jpeg';
    """
    instas = query(q)

    log("Collecting Instagram photos...")
    progress(0, len(instas))
    for i, l in enumerate(instas):
        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]

        photo = absolutepath  # TODO refactor

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id) + "instagram_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = filename_prefix + pre + ext.lower()
        shutil.copyfile(photo, targetphoto)
        tally("insta")

        tally("total")
        progress(i+1, len(instas))

        # TODO figure out how to get real date? is that even possible?

def get_and_collect_videos():
    log("Querying database for videos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               a.filePath AS attachment
        FROM RKMaster m LEFT JOIN RKAttachment a on m.uuid = a.attachedToUuid
        WHERE UTI = 'com.apple.quicktime-movie';
    """
    videos = query(q)

    log("Collecting videos and real-time, high-framerate versions of slomos...")
    progress(0, len(videos))
    for i, l in enumerate(videos):
        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]
        attachment = l[3]

        video = absolutepath  # TODO refactor

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id)
        if attachment:
            filename_prefix = filename_prefix + "slomo_"

        # copy video
        pre, ext = os.path.splitext(os.path.basename(video))
        targetvideo = filename_prefix + pre + ext.lower()
        shutil.copyfile(video, targetvideo)
        tally("videos")

        tally("total")
        progress(i+1, len(videos))

def get_matching_slomos(videos):

    # TODO rendered versions of slomos (see resources/media/version)
    # iff hasAttachments = 1? this also catches jpeg screenshots, good
    # join with RKAttachment table but that doesn't help much? reference to AEE/plist files that contain <string>com.apple.video.slomo</string> but nothing more; could try matching based on IMG_... "Creation Date" == fullsizeoutput... "Date Time Original" EXIF data
    # select * from RKMaster where fileName = 'IMG_0027.MOV';
    # 26|KnsExJJpSZeaq84eAnGU+g|AeIEW9HoEl03RoFYWMwASoRoBIfF|1|IMG_0027|567696357.092434|0||0|0|0|0|0|1.72166666666667|559410555|||4964099|720|1280|com.apple.quicktime-movie|EntveYbqTVaNWcxAB%yk+A||IMG_0027|IMG_0027.MOV|0|0|1|0|2018/12/28/20181228-132551/IMG_0027.MOV|559410555|559410555|IMG_0027.MOV|4964099|3|||1|7200|||1||

    pass

def get_and_collect_bursts():
    log("Querying database for burst photos...")

    q = """
        SELECT modelId AS id,
               imagePath AS absolutepath,
               fileCreationDate AS creationdate,
               burstUuid AS burstid
        FROM RKMaster
        WHERE burstUuid IS NOT NULL
    """
    bursts = query(q)

    # TODO RKVersion contains column burstPickType indicating (?) which image was chosen as the "hero" image

    log("Collecting burst photos...")
    progress(0, len(bursts))
    for i, l in enumerate(bursts):
        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]
        burstid = l[3]

        photo = absolutepath  # TODO refactor

        # assemble filename prefix
        filename_prefix = assemble_file_name_prefix(creationdate, id) + "burst_" + burstid + "_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = filename_prefix + pre + ext.lower()
        shutil.copyfile(photo, targetphoto)
        tally("bursts")

        tally("total")
        progress(i+1, len(bursts))

def get_and_collect_panoramas():
    log("Querying database for panoramas...")

    q = """
        SELECT modelId AS id,
               imagePath AS absolutepath,
               fileCreationDate AS creationdate
        FROM RKMaster
        WHERE UTI = 'public.heic'
        AND NOT (
           (mediaGroupId IS NOT NULL AND UTI = 'public.heic')  -- live photo
        -- TODO following should not be necessary here?
        --OR (mediaGroupId IS NOT NULL AND UTI = 'public.jpeg')  -- instagram photo
        --OR (UTI = 'com.apple.quicktime-movie')  -- video
        --OR (burstUuid IS NOT NULL)  -- burst
        --OR (UTI = 'public.mpeg-4')  -- whatsapp movie
        --OR (UTI = 'public.jpeg' AND burstUuid IS NULL AND mediaGroupId IS NULL)  -- whatsapp image
        --OR (UTI = 'public.png')  -- screenshot
        )
    """
    panoramas = query(q)

    log("Collecting panoramas and creating JPEG versions...")
    progress(0, len(panoramas))
    for i, l in enumerate(panoramas):
        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]

        photo = absolutepath  # TODO refactor

        # assemble filename prefix  # TODO abstract this
        filename_prefix = assemble_file_name_prefix(creationdate, id) + "panorama_"

        # copy photo  # TODO abstract
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = filename_prefix + pre + ext.lower()
        shutil.copyfile(photo, targetphoto)
        tally("panoramas")

        # TODO generate jpeg version!

        # create jpeg version
        targetjpeg = filename_prefix + pre + ".jpg"
        jpeg_from_heic(photo, targetjpeg)
        tally("panoramajpeg")

        tally("total")
        progress(i+1, len(panoramas))

def count_up_other_known_kinds_of_images():
    log("Querying database for WhatsApp images and videos...")
    # TODO etc., tally()

# TODO hdr images?

def main():
    create_working_copy_of_photos_db()
    init_target()

    # TODO store most recent import uuid or a set of all of them in metadata file, then proceed based on that (only export newer photos) if it‘s present. also keep cache of video metadata in there?
    # TODO "that means you've run this thingy most recently between x and y (can get this info based on grouping by import id and getting min/mix timestamp for the newest known and oldest unknown). correct?"

    get_and_collect_and_convert_live_photos()  # TODO just "photos"
    get_and_collect_videos()
    get_and_collect_bursts()
    get_and_collect_panoramas()
    get_and_collect_insta_photos()

    count_up_other_known_kinds_of_images()

    stats()

if __name__ == "__main__":
    main()

# TODO what to do about screenshots, whatsapp pics, other random pics, vids, non-live photos etc.?

# TODO then what to do about pictures left on phone? delete all non-whatsapp ones? all IMG_xxx then, but how?
# TODO how to deal with multiple imports? add -import parameter that adds the corresponding predicate (master table has import id column) to the queries?

# https://github.com/xgess/timestamp_all_photos/blob/master/app/apple_photos_library.py
# https://github.com/SummittDweller/merge-photos-faces/blob/master/main.py
