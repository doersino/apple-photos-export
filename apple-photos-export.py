import os
import sys
import glob
import shutil
import subprocess
import sqlite3
import atexit
from datetime import datetime
import argparse
import json

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

def table(assoc):
    key_width = max([0] + [len(str(k)) for k in assoc.keys()])
    #val_width = max([0] + [len(str(k)) for k in assoc.values()])

    for k, v in assoc.items():
        print(str(k).ljust(key_width) + " | " + str(v))

############################
# PARSE ARGUMENTS & CONFIG #
############################

log("Processing arguments...")
parser = argparse.ArgumentParser()
parser.add_argument("target", metavar="TARGET", type=str, help="target directory (should also contain configuration)")
parser.add_argument("-v", "--verbose", action="store_true", dest="verbose", default=False, help="output more verbose status messages")
args = parser.parse_args()

# Target folder.
TARGET = args.target

# Verbosity.
VERBOSE = args.verbose

log("Parsing configuration file (thereby also checking if target directory exists)...")
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
# from Dropbox etc. also show up here, but for the most part don't follow the
# "IMG_XXXX" naming scheme.
# For each import, a separate folder "YYYY/MM/DD/timestamp" exists.
MASTERS = os.path.join(LIBRARY, "Masters")

# Videos associated with live photos (possibly among other things), joinable
# through "Content Identifier" EXIF property. Subfolders exist.
MASTER = os.path.join(LIBRARY, "resources/media/master")

# Slomo videos actually rendered as slomos, edited variants of photos that have
# been edited on the phone, etc.
VERSION = os.path.join(LIBRARY, "resources/media/version")

# Import groups to be ignored (i.e. those that have already been processed).
IGNORE_IMPORT_GROUPS = []

# Mapping between content identifiers and file names for live photo videos.
LIVE_PHOTO_VIDEOS = {}

# Media files written to temporary storage. If the user confirms that everything
# went smoothly, these will be copied to the TARGET.
TMP_FILES = []


################################################################################

# WHERE predicates for known media types (where m is RKMaster)
IS_PHOTO = """
m.UTI = 'public.heic'
AND (m.mediaGroupId IS NOT NULL OR m.groupingUuid IS NOT NULL)
"""

IS_VIDEO = """
UTI = 'com.apple.quicktime-movie'
"""

IS_BURST = """
m.UTI = 'public.jpeg'
AND m.burstUuid IS NOT NULL
AND NOT (substr(m.filename, 9, 1) = '-'
         AND substr(m.filename, 14, 1) = '-'
         AND substr(m.filename, 19, 1) = '-'
         AND substr(m.filename, 24, 1) = '-'
         AND length(m.filename) = 40)
"""

IS_PANORAMA = """
m.UTI = 'public.heic'
AND m.mediaGroupId IS NULL
AND m.groupingUuid IS NULL
AND m.width <> m.height
"""

IS_SQUARE = """
m.UTI = 'public.heic'
AND (m.mediaGroupId IS NULL AND m.groupingUuid IS NULL)
AND m.width = m.height
"""

IS_INSTA = """
m.UTI = 'public.jpeg'
AND (m.mediaGroupId IS NOT NULL
     OR (m.burstUuid IS NOT NULL
         AND (substr(m.filename, 9, 1) = '-'
              AND substr(m.filename, 14, 1) = '-'
              AND substr(m.filename, 19, 1) = '-'
              AND substr(m.filename, 24, 1) = '-'
              AND length(m.filename) = 40)))
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

def pnot(pred):
    return " NOT (" + pred + ")"

# TODO also keep cache of video metadata in this file – will cut down on long wait right at the start
# TODO maybe: "that means you've run this thingy most recently between x and y (can get this info based on grouping by import id and getting min/mix timestamp for the newest known and oldest unknown)"
def read_cache():
    log("Reading and processing cache (list of already-exported Apple Photos imports, live photo video index)...")

    global IGNORE_IMPORT_GROUPS
    global LIVE_PHOTO_VIDEOS
    try:
        with open(os.path.join(TARGET, "apple-photos-export.json"), "r") as f:
            data = json.load(f)
            IGNORE_IMPORT_GROUPS = data['IGNORE_IMPORT_GROUPS']
            LIVE_PHOTO_VIDEOS = data['LIVE_PHOTO_VIDEOS']
    except FileNotFoundError:
        pass

def only_relevant_import_groups():
    return "m.importGroupUuid NOT IN (" + ','.join(["'" + g + "'" for g in IGNORE_IMPORT_GROUPS]) + ")"

def write_cache():
    log("Updating cache (list of already-exported Apple Photos imports, live photo video index)...")

    q = "SELECT DISTINCT m.importGroupUuid FROM RKMaster m ORDER BY m.modelId"
    import_groups = [r[0] for r in query(q)]

    data = {
        'IGNORE_IMPORT_GROUPS': import_groups,
        'LIVE_PHOTO_VIDEOS': LIVE_PHOTO_VIDEOS
    }

    with open(os.path.join(TARGET, "apple-photos-export.json"), "w") as f:
        json.dump(data, f)

# as a sanity check, keep track of number of photos processed
TALLY = {"written": {}, "ignored": {}, "total": {}}
def tally(mode, category):
    global TALLY
    if category not in TALLY[mode].keys():
        TALLY[mode][category] = 1
    else:
        TALLY[mode][category] = TALLY[mode][category] + 1

def stats():
    q = """
        SELECT COUNT(*)
        FROM RKMaster m
    """ + pred(only_relevant_import_groups())
    items = query(q)[0][0]
    for i in range(items):
        tally("total", "In database")

    log("Summary:")
    log("The following media were successfully exported:", "info")
    table(TALLY["written"])

    log("These media were found, but ignored:", "info")
    table(TALLY["ignored"])

    log("In total:", "info")
    table(TALLY["total"])

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
        # TODO log("The file" + targetpath + " already existed, I overwrote it, "warn")
        progress(i+1, len(TMP_FILES), os.path.basename(tmppath))

def clean_up():
    log("Cleaning up...")
    log("Removing " + TMP + "...", "info")
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)

def create_working_copy_of_photos_db():
    os.makedirs(TMP, exist_ok=True)
    shutil.copyfile(DATABASE, TMP_DB)

def collect_photos():
    log("Querying database for photos and live photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               m.mediaGroupId AS contentidentifier,
               v.selfPortrait AS selfie
        FROM RKMaster m LEFT JOIN RKVersion v ON m.uuid = v.masterUuid
    """ + pred(IS_PHOTO, only_relevant_import_groups())
    photos = query(q)

    log("Completing index of live photo videos...")
    mov_files = glob.iglob(MASTER + '/**/*.mov', recursive=True)
    mov_files = [p for p in mov_files if p not in LIVE_PHOTO_VIDEOS.values()]

    if mov_files:
        with exif.ExifTool() as et:
            log("Batch-extracting metadata (this might take a minute or three)...", "info")
            metadata = et.get_metadata_batch(mov_files)

        new_live_photo_videos = {}
        log("Looking for QuickTime:ContentIdentifier fields...", "info")
        for d in metadata:
            try:
                new_live_photo_videos[d["QuickTime:ContentIdentifier"]] = d["SourceFile"]
            except KeyError:
                log("Couldn't find QuickTime:ContentIdentifier field for " + d["System:FileName"] + ", will ignore", "warn")

        global LIVE_PHOTO_VIDEOS
        LIVE_PHOTO_VIDEOS = {**LIVE_PHOTO_VIDEOS, **new_live_photo_videos}

    log("Matching live photos with corresponding video files...")
    photos2 = [] # id, date, photo file, video file
    for l in photos:
        id = l[0]
        photopath = MASTERS + "/" + l[1]
        creationdate = l[2]
        contentidentifier = l[3]
        selfie = bool(l[4])
        try:
            videopath = LIVE_PHOTO_VIDEOS[contentidentifier]  # TODO could also be new_live_photo_videos?
            photos2.append(tuple([id, photopath, creationdate, videopath, selfie]))
        except KeyError:
            log("Couldn't find live photo video file for " + photopath + ", will keep it without a video", "warn")
            photos2.append(tuple([id, photopath, creationdate, None, selfie]))

    # TODO get rendered variants of photos that were edited in the camera/photos app:
    # build and match contentid index of files in resources/media/version => edited photos and corresponding edited live videos
    # for edited photos, RKVersion.adjustmentUuid is not UNADJUSTEDNONRAW
    # can match edited photo and edited live video based on [Apple]         ContentIdentifier               : 0ABA71EB-76B4-410E-9F82-EB42D63F4B2D
    # and [QuickTime]     ContentIdentifier               : 0ABA71EB-76B4-410E-9F82-EB42D63F4B2D
    # but how to match with original/raw photo? also just based on time (modificationdate, photo/video create date) like video? ugh
    # => seems error-prone, so not gonna do this for now

    log("Collecting photos and corresponding live photo video files and creating JPEG versions...")
    progress(0, len(photos2))
    for i, (id, photopath, creationdate, videopath, selfie) in enumerate(photos2):

        # assemble filename prefix
        filename_prefix = assemble_filename_prefix(creationdate, id)
        if selfie:
            filename_prefix = filename_prefix + "selfie_"

        # copy photo and create jpeg version
        export_file(photopath, filename_prefix)
        tally("written", "Photos")
        tally("written", "Photos as JPEG")

        # copy live video if it exists
        if videopath:
            export_file(videopath, filename_prefix)
            tally("written", "Live photo videos")

        tally("total", "Considered")
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
    """ + pred(IS_VIDEO, only_relevant_import_groups())
    videos = query(q)

    log("Building index of rendered slomo videos...")
    rendered_slomo_videos = {}

    mov_files = glob.iglob(VERSION + '/**/fullsizeoutput_*.mov', recursive=True)
    if list(mov_files):
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
            if attachment:  # only in this case we expect a rendered slomo  # TODO move this predicate up
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
        tally("written", "Videos")

        # copy rendered slomo video if it exists
        if renderedslomopath:
            export_file(renderedslomopath, filename_prefix + "rendered_")
            tally("written", "Rendered slomos")

        tally("total", "Considered")
        progress(i+1, len(videos2), os.path.basename(videopath))

def collect_bursts():
    log("Querying database for burst photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               m.burstUuid AS burstid
        FROM RKMaster m
    """ + pred(IS_BURST, only_relevant_import_groups())
    bursts = query(q)

    # TODO RKVersion contains column burstPickType indicating (weirdly?) which image was chosen as the "hero" image

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
        tally("written", "Burst mode photos")

        tally("total", "Considered")
        progress(i+1, len(bursts), os.path.basename(burstpath))

def collect_panoramas():
    log("Querying database for panoramas...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + pred(IS_PANORAMA, only_relevant_import_groups())
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
        tally("written", "Panoramas")
        tally("written", "Panoramas as JPEG")

        tally("total", "Considered")
        progress(i+1, len(panoramas), os.path.basename(panoramapath))

def collect_squares():
    log("Querying database for square photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + pred(IS_SQUARE, only_relevant_import_groups())
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
        tally("written", "Square photos")
        tally("written", "Square photos as JPEG")

        tally("total", "Considered")
        progress(i+1, len(squares), os.path.basename(squarepath))

def collect_insta_photos():
    log("Querying database for Instagram photos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate
        FROM RKMaster m
    """ + pred(IS_INSTA, only_relevant_import_groups())
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
        tally("written", "Instagrammed photos")

        tally("total", "Considered")
        progress(i+1, len(instas), os.path.basename(instapath))

        # TODO figure out how to get actual date? is that even possible? => from original file creation/edit date

def tally_other_known_media():
    log("Querying database for other known but irrelevant kinds of images...")

    log("Tallying Screenshots...", "info")
    q = "SELECT 1 FROM RKMaster m" + pred(IS_SCREENSHOT, only_relevant_import_groups())
    screenshots = query(q)
    for l in screenshots:
        tally("ignored", "Screenshots")
        tally("total", "Considered")

    log("Tallying WhatsApp images...", "info")
    q = "SELECT 1 FROM RKMaster m" + pred(IS_WHATSAPP_PHOTO, only_relevant_import_groups())
    whatsapp_images = query(q)
    for l in whatsapp_images:
        tally("ignored", "WhatsApp images")
        tally("total", "Considered")

    log("Tallying WhatsApp videos...", "info")
    q = "SELECT 1 FROM RKMaster m" + pred(IS_WHATSAPP_VIDEO, only_relevant_import_groups())
    whatsapp_videos = query(q)
    for l in whatsapp_videos:
        tally("ignored", "WhatsApp videos")
        tally("total", "Considered")

def list_unknown_media():
    log("The following media could not be categorized (you'll have to copy these manually if you need them):")

    q = "SELECT m.imagePath FROM RKMaster m" + pred(
        pnot(IS_PHOTO),
        pnot(IS_VIDEO),
        pnot(IS_BURST),
        pnot(IS_PANORAMA),
        pnot(IS_SQUARE),
        pnot(IS_INSTA),
        pnot(IS_SCREENSHOT),
        pnot(IS_WHATSAPP_PHOTO),
        pnot(IS_WHATSAPP_VIDEO),
        only_relevant_import_groups())
    unknowns = query(q)
    for l in unknowns:
        print(MASTERS + "/" + l[0])
        tally("ignored", "Unknown/uncategorized media")
        tally("total", "Considered")

def main():
    atexit.register(clean_up)

    create_working_copy_of_photos_db()
    read_cache()

    collect_photos()
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
        sys.exit(-1)  # cleanup will happen automatically

    persist_files_to_target()
    write_cache()

if __name__ == "__main__":
    main()
