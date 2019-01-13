import os
import sys
import glob
import shutil
import subprocess
import sqlite3
from datetime import datetime
from argparse import ArgumentParser

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
# TODO add proper license etc.
def progress(count, total, status=''):
    if total == 0:
        return

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


############################
# PARSE ARGUMENTS & CONFIG #
############################

log("TODO parsing arguments")
parser = ArgumentParser()
parser.add_argument("target", metavar="TARGET", type=str, help="target directory (should also contain configuration)")
parser.add_argument("-q", "--quiet", action="store_false", dest="verbose", default=True, help="don't print status messages to stdout")
args = parser.parse_args()

# Target folder.
TARGET = args.target

# Do not print any non-warning, non-error output?
VERBOSE = args.verbose

log("TODO parsing config file")
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

# TODO Slomo videos actually rendered as slomos etc. (And more?) In subfolders.
#VERSION  = os.path.join(LIBRARY, "resources/media/version")

# Import groups to be ignored (i.e. those that have already been processed).
ONLY_RELEVANT_IMPORT_GROUPS = []

# Media files written to temporary storage. If the user confirms that everything
# went smoothly, these will be copied to the TARGET.
TMP_FILES = []


########
# TODO #
########

# TODO cleanup imports, really only import required functions

# TODO use os.path.join throughout, especially in assemble_prefix thingy

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

################################################################################

# query helper
def query(q):
    conn = sqlite3.connect(TMP_DB)
    c = conn.cursor()
    c.execute(q)
    res = list(c)
    conn.close()
    return res

def get_relevant_import_groups():
    log("Reading list of already-processed Apple Photos imports...")

    ignore_import_groups = []
    try:
        with open(os.path.join(TARGET, "apple-photos-export.lst"), "r") as lst:
            ignore_import_groups = [line.strip() for line in lst.readlines()]
    except FileNotFoundError:
        pass

    #q = "SELECT DISTINCT ig.uuid FROM RKImportGroup ig"  # sometimes import groups apparently get culled from this table by Photos.app?
    q = "SELECT DISTINCT m.importGroupUuid FROM RKMaster m"
    import_groups = query(q)

    global ONLY_RELEVANT_IMPORT_GROUPS
    for l in import_groups:
        if l[0] not in ignore_import_groups:
            ONLY_RELEVANT_IMPORT_GROUPS.append(l[0])

    print(ignore_import_groups)
    print(ONLY_RELEVANT_IMPORT_GROUPS)

def only_relevant_import_groups_sql():
    return "AND m.importGroupUuid IN (" + ','.join(["'" + g + "'" for g in ONLY_RELEVANT_IMPORT_GROUPS]) + ")"

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
        WHERE true
    """ + only_relevant_import_groups_sql()
    items = query(q)[0][0]
    print(items)

def log_file(path):
    global TMP_FILES
    TMP_FILES.append(path)

def assemble_filename_prefix(creationdate, id):
    ts = creationdate + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason
    # TODO maybe convert to system time zone? or "taken" time zone? this info can be found in the versions table
    datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    datestring = datestring.replace(" ", "_").replace(":", "-")
    yearmonth = datetime.utcfromtimestamp(ts).strftime('%Y/%m_%B')
    directory = os.path.join(TMP, yearmonth)
    filename_prefix = os.path.join(directory, datestring + "_" + str(id) + "_")
    return filename_prefix

def jpeg_from_heic(heicfile, jpegfile, quality=80):  # TODO setting for quality?
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
        progress(i+1, len(TMP_FILES), os.path.basename(tmppath))

def clean_up():
    log("Cleaning up...")
    log("Removing " + TMP + "...", "info")
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
        WHERE
    """ + IS_PHOTO + only_relevant_import_groups_sql()
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
        if videopath is not None:
            export_file(videopath, filename_prefix)
            tally("livephotovideo")

        tally("total")
        progress(i+1, len(photos2), os.path.basename(photopath))

    # hdr images are already "baked in", the non-hdr version that briefly shows up on the phone seems to not be exported

def collect_videos():
    log("Querying database for videos...")

    q = """
        SELECT m.modelId AS id,
               m.imagePath AS absolutepath,
               m.fileCreationDate AS creationdate,
               a.filePath AS attachment
        FROM RKMaster m LEFT JOIN RKAttachment a on m.uuid = a.attachedToUuid
        WHERE
    """ + IS_VIDEO + only_relevant_import_groups_sql()
    videos = query(q)

    log("Collecting videos and real-time, high-framerate versions of slomos...")
    progress(0, len(videos))
    for i, l in enumerate(videos):
        id = l[0]
        videopath = MASTERS + "/" + l[1]
        creationdate = l[2]
        attachment = l[3]

        # assemble filename prefix
        filename_prefix = assemble_filename_prefix(creationdate, id)
        if attachment:  # TODO maybe use exiftool to look at framerate instead?
            filename_prefix = filename_prefix + "slomo_"

        # copy video
        export_file(videopath, filename_prefix)
        tally("videos")

        tally("total")
        progress(i+1, len(videos), os.path.basename(videopath))

# TODO timelapses: framerate 30 (instead of ~60 vs. ~240) and also: [Track1]        ComApplePhotosCaptureMode       : Time-lapse

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
        WHERE
    """ + IS_BURST + only_relevant_import_groups_sql()
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
        WHERE
    """ + IS_PANORAMA + only_relevant_import_groups_sql()
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
        WHERE
    """ + IS_SQUARE + only_relevant_import_groups_sql()
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
        WHERE
    """ + IS_INSTA + only_relevant_import_groups_sql()
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
    q = "SELECT 1 FROM RKMaster m WHERE" + IS_SCREENSHOT + only_relevant_import_groups_sql()
    screenshots = query(q)
    for l in screenshots:
        tally("screenshot")
        tally("total")

    log("Tallying WhatsApp images...", "info")
    q = "SELECT 1 FROM RKMaster m WHERE" + IS_WHATSAPP_PHOTO + only_relevant_import_groups_sql()
    whatsapp_images = query(q)
    for l in whatsapp_images:
        tally("whatsapp_image")
        tally("total")

    log("Tallying WhatsApp videos...", "info")
    q = "SELECT 1 FROM RKMaster m WHERE" + IS_WHATSAPP_VIDEO + only_relevant_import_groups_sql()
    whatsapp_videos = query(q)
    for l in whatsapp_videos:
        tally("whatsapp_video")
        tally("total")

def list_unknown_media():
    log("The following media could not be categorized (you'll have to copy these manually if you need them)...")

    q = ("SELECT m.imagePath FROM RKMaster m WHERE NOT (("
         + IS_PHOTO + ") OR ("
         + IS_VIDEO + ") OR ("
         + IS_BURST + ") OR ("
         + IS_PANORAMA + ") OR ("
         + IS_SQUARE + ") OR ("
         + IS_INSTA + ") OR ("
         + IS_SCREENSHOT + ") OR ("
         + IS_WHATSAPP_PHOTO + ") OR ("
         + IS_WHATSAPP_VIDEO + "))" + only_relevant_import_groups_sql())
    unknowns = query(q)
    for l in unknowns:
        print(MASTERS + "/" + l[0])
        tally("unknown")
        tally("total")

def main():
    create_working_copy_of_photos_db()

    get_relevant_import_groups()
    print(only_relevant_import_groups_sql())

    # TODO store most recent import uuid or a set of all of them in metadata
    # file (read in init_target()), then proceed based on that (only export newer
    # photos) if it‘s present. also keep cache of video metadata in there maybe?
    # TODO "that means you've run this thingy most recently between x and y (can
    # get this info based on grouping by import id and getting min/mix timestamp
    # for the newest known and oldest unknown). correct?"

    #collect_and_convert_photos()
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
    clean_up()

if __name__ == "__main__":
    main()

# TODO what to do about screenshots, whatsapp pics, other random pics, non-live photos etc.?

# TODO then what to do about pictures left on phone? delete all non-whatsapp ones? all IMG_xxx then, but how?
# TODO how to deal with multiple imports? add -import parameter that adds the corresponding predicate (master table has import id column) to the queries?

# https://github.com/xgess/timestamp_all_photos/blob/master/app/apple_photos_library.py
# https://github.com/SummittDweller/merge-photos-faces/blob/master/main.py

