import os
import sys
import glob
import shutil
import subprocess
import sqlite3
import atexit
from datetime import datetime
import argparse

import configparser

import pyexiftool.exiftool as exif

###########
# HELPERS #
###########

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
def progress(count, total, status=''):

    # don't print empty progress bars
    if total == 0:
        return

    # erase previous progres bar (\b moves cursor one character backward)
    sys.stdout.write('\b' * progress.prev_length
                     +  ' ' * progress.prev_length
                     + '\b' * progress.prev_length)

    # print progress bar
    bar_len = 50
    filled_len = int(round(bar_len * count / float(total)))

    bar = '=' * filled_len + '-' * (bar_len - filled_len)
    percents = round(100.0 * count / float(total), 1)
    items = str(count) + "/" + str(total)

    progress_bar = '[%s] %s%s (%s)' % (bar, percents, '%', items)
    if status:
        progress_bar = progress_bar +' -> %s' % (status)
    sys.stdout.write(progress_bar + '\r')

    # store length of current progress bar for future erasing
    progress.prev_length = len(progress_bar)

    # print newline upon completion to prevent overwriting by subsequent output
    if count == total:
        sys.stdout.write('\n')
        sys.stdout.flush()
progress.prev_length = 0


############################
# PARSE ARGUMENTS & CONFIG #
############################

log("Parsing arguments...")
parser = argparse.ArgumentParser()
parser.add_argument("target", metavar="TARGET", type=str, help="target directory (should also contain configuration)")
#parser.add_argument("-q", "--quiet", action="store_false", dest="verbose", default=True, help="don't print status messages to stdout")
args = parser.parse_args()

# Target folder.
TARGET = args.target

# Do not print any non-warning, non-error output?
#VERBOSE = args.verbose

log("Parsing configuration file, thereby also checking if target exists...")
CONFIG = os.path.join(TARGET, "apple-photos-export.ini")
conf = configparser.ConfigParser()
conf.read(CONFIG)

# Absolute path to the .photoslibrary package.
LIBRARY = conf["Paths"]["ApplePhotosLibrary"]

# Temporary storage.
TMP = conf["Paths"]["TemporaryStorage"]

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

# Slomo videos actually rendered as slomos, edited variants of photos that have
# been edited on Photos on the phone, etc.
VERSION = os.path.join(LIBRARY, "resources/media/version")

# Import groups to be ignored (i.e. those that have already been processed).
ONLY_RELEVANT_IMPORT_GROUPS = []

# Media files written to temporary storage. If the user confirms that everything
# went smoothly, these will be copied to the TARGET.
TMP_FILES = []


########
# TODO #
########

# hmm, what happens if a picture is edited in the camera/photos app? original in versions table? try!
# how does it interact with apps like halide?
# test more! icloud? etc.

# put this or something similar into the documentation:
# SELECT modelId AS id,
#     imagePath AS absolutepath,
#     fileCreationDate AS creationdate,
#     mediaGroupId AS contentidentifier,  -- if set, need to get live photo video
#     burstUuid AS burstid,               -- if set, could group burst mode pics
#     UTI AS type,                        -- public.heic, public.jpeg (whatsapp/burst/pano), com.apple.quicktime-movie, public.png, public.mpeg-4 (whatsapp movies)
#     importGroupUuid AS importid,
#     hasAttachments AS isslomoorhasjpegscreenshot
# FROM RKMaster

################################################################################

# WHERE predicates for known media types (where m is RKMaster)
IS_PHOTO = """
m.mediaGroupId IS NOT NULL
AND m.UTI = 'public.heic'
"""

IS_VIDEO = """
UTI = 'com.apple.quicktime-movie'
"""

IS_BURST = """
m.UTI = 'public.jpeg'
AND m.burstUuid IS NOT NULL
"""

IS_PANORAMA = """
m.UTI = 'public.heic'
AND m.mediaGroupId IS NULL
AND m.width <> m.height
"""

IS_SQUARE = """
m.UTI = 'public.heic'
AND m.mediaGroupId IS NULL
AND m.width = m.height
"""

IS_INSTA = """
m.mediaGroupId IS NOT NULL
AND m.UTI = 'public.jpeg'
"""

IS_SCREENSHOT = """
m.UTI = 'public.png'
AND m.filename LIKE 'IMG_%'
"""

IS_WHATSAPP = """
AND substr(m.filename, 9, 1) = '-'
AND substr(m.filename, 14, 1) = '-'
AND substr(m.filename, 19, 1) = '-'
AND substr(m.filename, 24, 1) = '-'
AND length(m.filename) = 40
"""

IS_WHATSAPP_PHOTO = """
m.UTI = 'public.jpeg'
AND m.burstUuid IS NULL
AND m.mediaGroupId IS NULL
""" + IS_WHATSAPP

IS_WHATSAPP_VIDEO = """
m.UTI = 'public.mpeg-4'
""" + IS_WHATSAPP

# query helper
def query(q):
    conn = sqlite3.connect(TMP_DB)
    c = conn.cursor()
    c.execute(q)
    res = list(c)
    conn.close()
    return res

def pred(*preds):
    return " WHERE (" + ") AND (".join(preds) + ")"

# TODO also keep cache of video metadata in this file – will cut down on long wait right at the start
def get_relevant_import_groups():
    log("Processing list of already-exported Apple Photos imports...")

    log("Reading list...", "info")
    ignore_import_groups = []
    try:
        with open(os.path.join(TARGET, "apple-photos-export.lst"), "r") as lst:
            ignore_import_groups = [line.strip() for line in lst.readlines()]
    except FileNotFoundError:
        pass

    log("Querying database for all import groups...", "info")
    #q = "SELECT DISTINCT ig.uuid FROM RKImportGroup ig"  # sometimes import groups apparently get culled from this table by Photos.app?
    q = "SELECT DISTINCT m.importGroupUuid FROM RKMaster m"
    import_groups = query(q)

    log("Filtering out already-processed import groups...", "info")
    global ONLY_RELEVANT_IMPORT_GROUPS
    for l in import_groups:
        if l[0] not in ignore_import_groups:
            ONLY_RELEVANT_IMPORT_GROUPS.append(l[0])

def only_relevant_import_groups_sql():
    return "m.importGroupUuid IN (" + ','.join(["'" + g + "'" for g in ONLY_RELEVANT_IMPORT_GROUPS]) + ")"

def write_processed_import_groups():
    log("Updating list of already-processed Apple Photos imports...")

    with open(os.path.join(TARGET, "apple-photos-export.lst"), "a") as lst:
        for l in ONLY_RELEVANT_IMPORT_GROUPS:
            lst.write(l + "\n")

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
    log("Summary:")
    log("The following media types were found TODO", "info")
    for category, count in TALLY.items():
        print(category + ": " + str(count))

    log("The photos library contains this many items.", "info")
    q = """
        SELECT COUNT(*)
        FROM RKMaster m
    """ + pred(only_relevant_import_groups_sql())
    items = query(q)[0][0]
    print(items)

def log_file(path):
    global TMP_FILES
    TMP_FILES.append(path)

def weird_apple_timestamp_to_unix(ts):
    return int(ts) + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason

def assemble_filename_prefix(creationdate, id):
    ts = weird_apple_timestamp_to_unix(creationdate)
    # TODO maybe convert to system time zone? or "taken" time zone? this info can be found in the versions table
    datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    datestring = datestring.replace(" ", "_").replace(":", "-")
    yearmonth = datetime.utcfromtimestamp(ts).strftime('%Y/%m_%B')
    directory = os.path.join(TMP, yearmonth)
    filename_prefix = os.path.join(directory, datestring + "_" + str(id) + "_")
    return filename_prefix

def jpeg_from_heic(heicfile, jpegfile, quality=80):
    try:
        subprocess.check_output(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), heicfile, "--out", jpegfile])
    except subprocess.CalledProcessError as err:
        log("sips failed: " + repr(err), "error")

def export_file(sourcepath, prefix):

    # create intermediate directories if required
    directory = os.path.dirname(prefix)
    os.makedirs(directory, exist_ok=True)

    # copy file
    name, ext = os.path.splitext(os.path.basename(sourcepath))
    targetpath = prefix + name + ext.lower()
    shutil.copyfile(sourcepath, targetpath)
    log_file(targetpath)

    # create jpeg version of heic images
    if "HEIC" in ext:
        targetjpegpath = prefix + name + ".jpg"
        jpeg_from_heic(sourcepath, targetjpegpath)
        log_file(targetjpegpath)

def persist_files_to_target():
    log("Persisting exported media files to target...")
    progress(0, len(TMP_FILES))
    for i, tmppath in enumerate(TMP_FILES):
        rel = os.path.relpath(tmppath, TMP)
        targetpath = os.path.join(TARGET, rel)
        directory = os.path.dirname(targetpath)
        os.makedirs(directory, exist_ok=True)
        shutil.copyfile(tmppath, targetpath)
        # log("The file" + targetpath + " already existed, I overwrote it, "warn")
        progress(i+1, len(TMP_FILES), os.path.basename(tmppath))

def clean_up():
    log("Cleaning up...")
    log("Removing " + TMP + "...", "info")
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)

def create_working_copy_of_photos_db():
    os.makedirs(TMP, exist_ok=True)
    shutil.copyfile(DATABASE, TMP_DB)

def collect_and_convert_photos():
    log("Querying database for photos and live photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               m.mediaGroupId AS contentidentifier,
               v.selfPortrait AS selfie
        FROM RKMaster m LEFT JOIN RKVersion v ON m.uuid = v.masterUuid
    """ + pred(IS_PHOTO, only_relevant_import_groups_sql())
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
            log("Couldn't find live photo video file for " + photopath + ", will keep it without a video", "warn")
            photos2.append(tuple([id, photopath, creationdate, None, selfie]))

    # TODO build and match contentid index of files in resources/media/version => edited photos and corresponding edited live videos
    # for edited photos, RKVersion.adjustmentUuid is not UNADJUSTEDNONRAW
    # can match edited photo and edited live video based on [Apple]         ContentIdentifier               : 0ABA71EB-76B4-410E-9F82-EB42D63F4B2D
    # and [QuickTime]     ContentIdentifier               : 0ABA71EB-76B4-410E-9F82-EB42D63F4B2D
    # TODO but how to match with original/raw photo? also just based on time like video? ugh
    attachment_files = None
    # ...
    photos3 = []
    # ...

    log("Collecting photos and corresponding live photo video files and creating JPEG versions...")
    progress(0, len(photos2))
    for i, (id, photopath, creationdate, videopath, selfie) in enumerate(photos2):

        # assemble filename prefix
        filename_prefix = assemble_filename_prefix(creationdate, id)
        if selfie:
            filename_prefix = filename_prefix + "selfie_"

        # copy photo and create jpeg version
        export_file(photopath, filename_prefix)
        tally("photo")
        tally("photojpeg")

        # copy live video if it exists
        if videopath:
            export_file(videopath, filename_prefix)
            tally("livephotovideo")

        tally("total")
        progress(i+1, len(photos2), os.path.basename(photopath))

def collect_videos():
    log("Querying database for videos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               a.filePath AS attachment,
               a.fileModificationDate AS modificationdate
        FROM RKMaster m LEFT JOIN RKAttachment a on m.uuid = a.attachedToUuid
    """ + pred(IS_VIDEO, only_relevant_import_groups_sql())
    videos = query(q)

    log("Building index of rendered slomo videos...")
    rendered_slomo_videos = {}

    mov_files = glob.iglob(VERSION + '/**/fullsizeoutput_*.mov', recursive=True)
    with exif.ExifTool() as et:
        log("Batch-extracting metadata (this might take a few seconds)...", "info")
        metadata = et.get_metadata_batch(mov_files)

    log("Looking for QuickTime:DateTimeOriginal fields...", "info")
    for d in metadata:
        try:
            modificationdate = d["QuickTime:DateTimeOriginal"]
            modificationdate_tz = int(datetime.strptime(modificationdate, "%Y:%m:%d %H:%M:%S%z").timestamp())
            rendered_slomo_videos[modificationdate_tz] = d["SourceFile"]
        except KeyError:
            log("Couldn't find QuickTime:DateTimeOriginal field for " + d["System:FileName"] + ", will ignore", "warn")

    log("Matching slomo videos with corresponding rendered slomo videos...")
    videos2 = [] # id, date, video file, rendered slomo file
    for l in videos:
        id = l[0]
        videopath = MASTERS + "/" + l[1]
        creationdate = l[2]
        attachment = l[3]
        modificationdate = l[4]
        try:
            renderedslomopath = rendered_slomo_videos[weird_apple_timestamp_to_unix(modificationdate)]
            videos2.append(tuple([id, videopath, creationdate, renderedslomopath]))
        except (KeyError, TypeError):
            if attachment:  # only in this case we expect a rendered slomo
                log("Couldn't find rendered slomo video for " + videopath + ", will keep it without one", "warn")
            videos2.append(tuple([id, videopath, creationdate, None]))

    log("Collecting videos: normal videos, timelapses, slomos (real-time, high-framerate versions) and rendered slomo videos...")
    # TODO timelapses: framerate 30 (instead of ~60 vs. ~240) and also: [Track1]        ComApplePhotosCaptureMode       : Time-lapse
    progress(0, len(videos2))
    for i, (id, videopath, creationdate, renderedslomopath) in enumerate(videos2):

        # assemble filename prefix
        filename_prefix = assemble_filename_prefix(creationdate, id)
        if renderedslomopath:
            filename_prefix = filename_prefix + "slomo_"

        # copy video
        export_file(videopath, filename_prefix)
        tally("video")

        # copy rendered slomo video if it exists
        if renderedslomopath:
            export_file(renderedslomopath, filename_prefix + "rendered_")
            tally("renderedslomovideo")

        tally("total")
        progress(i+1, len(videos2), os.path.basename(videopath))

    print(videos2)
    input()
    sys.exit(-2)

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
    """ + pred(IS_BURST, only_relevant_import_groups_sql())
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
        filename_prefix = assemble_filename_prefix(creationdate, id) + "burst_" + burstid + "_"

        # copy photo
        export_file(burstpath, filename_prefix)
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
    """ + pred(IS_PANORAMA, only_relevant_import_groups_sql())
    panoramas = query(q)

    log("Collecting panoramas and creating JPEG versions...")
    progress(0, len(panoramas))
    for i, l in enumerate(panoramas):
        id = l[0]
        panoramapath = MASTERS + "/" + l[1]
        creationdate = l[2]

        # assemble filename prefix  # TODO abstract this
        filename_prefix = assemble_filename_prefix(creationdate, id) + "panorama_"

        # copy photo and create jpeg version
        export_file(panoramapath, filename_prefix)
        tally("panoramas")
        tally("panoramajpeg")

        tally("total")
        progress(i+1, len(panoramas), os.path.basename(panoramapath))

def collect_squares():
    log("Querying database for square photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + pred(IS_SQUARE, only_relevant_import_groups_sql())
    squares = query(q)

    log("Collecting square photos and creating JPEG versions...")
    progress(0, len(squares))
    for i, l in enumerate(squares):
        id = l[0]
        squarepath = MASTERS + "/" + l[1]
        creationdate = l[2]

        # assemble filename prefix  # TODO abstract this
        filename_prefix = assemble_filename_prefix(creationdate, id) + "square_"

        # copy photo and create jpeg version
        export_file(squarepath, filename_prefix)
        tally("squares")
        tally("squarejpeg")

        tally("total")
        progress(i+1, len(squares), os.path.basename(squarepath))

def collect_insta_photos():
    log("Querying database for Instagram photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + pred(IS_INSTA, only_relevant_import_groups_sql())
    instas = query(q)

    log("Collecting Instagram photos...")
    progress(0, len(instas))
    for i, l in enumerate(instas):
        id = l[0]
        instapath = MASTERS + "/" + l[1]
        creationdate = l[2]

        # assemble filename prefix
        filename_prefix = assemble_filename_prefix(creationdate, id) + "instagram_"

        # copy photo
        export_file(instapath, filename_prefix)
        tally("insta")

        tally("total")
        progress(i+1, len(instas), os.path.basename(instapath))

        # TODO figure out how to get actual date? is that even possible? => from original file creation/edit date

def tally_other_known_media():
    log("Querying database for other known but irrelevant kinds of images...")

    log("Tallying Screenshots...", "info")
    q = "SELECT 1 FROM RKMaster m" + pred(IS_SCREENSHOT, only_relevant_import_groups_sql())
    screenshots = query(q)
    for l in screenshots:
        tally("screenshot")
        tally("total")

    log("Tallying WhatsApp images...", "info")
    q = "SELECT 1 FROM RKMaster m" + pred(IS_WHATSAPP_PHOTO, only_relevant_import_groups_sql())
    whatsapp_images = query(q)
    for l in whatsapp_images:
        tally("whatsapp_image")
        tally("total")

    log("Tallying WhatsApp videos...", "info")
    q = "SELECT 1 FROM RKMaster m" + pred(IS_WHATSAPP_VIDEO, only_relevant_import_groups_sql())
    whatsapp_videos = query(q)
    for l in whatsapp_videos:
        tally("whatsapp_video")
        tally("total")

def list_unknown_media():
    log("The following media could not be categorized (you'll have to copy these manually if you need them)...")

    q = "SELECT m.imagePath FROM RKMaster m" + pred(
        "NOT" + IS_PHOTO,
        "NOT" + IS_VIDEO,
        "NOT" + IS_BURST,
        "NOT" + IS_PANORAMA,
        "NOT" + IS_SQUARE,
        "NOT" + IS_INSTA,
        "NOT" + IS_SCREENSHOT,
        "NOT" + IS_WHATSAPP_PHOTO,
        "NOT" + IS_WHATSAPP_VIDEO,
        only_relevant_import_groups_sql())
    unknowns = query(q)
    for l in unknowns:
        print(MASTERS + "/" + l[0])
        tally("unknown")
        tally("total")

def main():
    atexit.register(clean_up)

    create_working_copy_of_photos_db()

    get_relevant_import_groups()

    # TODO "that means you've run this thingy most recently between x and y (can
    # get this info based on grouping by import id and getting min/mix timestamp
    # for the newest known and oldest unknown)"

    collect_and_convert_photos()
    collect_videos()
    collect_bursts()
    collect_panoramas()
    collect_squares()
    collect_insta_photos()

    tally_other_known_media()

    list_unknown_media()
    stats()

    response = input("All good (y/N)?")
    if response != "y":
        clean_up()
        sys.exit(-1)

    persist_files_to_target()
    write_processed_import_groups()

if __name__ == "__main__":
    main()

# TODO then what to do about pictures left on phone? delete all non-whatsapp ones? all IMG_xxx then, but how?

