import os
import sys
import glob
import shutil
import sqlite3
import subprocess
import pyexiftool.exiftool as exif

from datetime import datetime

# TODO cleanup imports, really only import required functions

#########
# USAGE #
#########

# See README.md.

##########
# CONFIG #
##########

# Absolute path to the .photoslibrary package.
LIBRARY = "/Users/noah/Pictures/Photos Library 2.photoslibrary"

# Temporary storage.
TMP = "/Users/noah/Desktop/test_tmp"

# Target folder.
TARGET = "/Users/noah/Desktop/test_target"

# Output verbosity.
VERBOSE = True

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

################################################################################

# TODO structure: helpers, etc.

def create_working_copy_of_photos_db():
    os.makedirs(TMP, exist_ok=True)
    shutil.copyfile(DATABASE, TMP_DB)

def init_target():
    os.makedirs(TARGET, exist_ok=True)

def query(q):
    conn = sqlite3.connect(TMP_DB)
    c = conn.cursor()
    c.execute(q)
    res = list(c)
    conn.close()
    return res

def get_masters():
    q = """
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
    return query(q)

def log(msg, type='status'):
    if type == "status":
        print('\033[1m' + msg + '\033[0m')
    if type == "info":
        print(msg)
    if type == "warn":
        print('\033[38;5;208m' + "⚠️  " + msg + '\033[0m')

# based on https://gist.github.com/vladignatyev/06860ec2040cb497f0f3
# TODO add proper license etc.
def progress(count, total, status=''):
    bar_len = 60
    filled_len = int(round(bar_len * count / float(total)))

    percents = round(100.0 * count / float(total), 1)
    bar = '=' * filled_len + '-' * (bar_len - filled_len)

    status = str(count) + "/" + str(total)

    sys.stdout.write('[%s] %s%s (%s)\r' % (bar, percents, '%', status))
    sys.stdout.flush()

def get_live_photo_videos():
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
    return vids

def get_live_photos(vids):
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
            log("Couldn't find video file for " + absolutepath + ", will ignore", "warn")
            live_idpvs.append(tuple([id, creationdate, absolutepath, None, selfie]))

    return live_idpvs

def collect_and_convert_live_photos(live_idpvs):
    # filenames: formattedcreationdate_id_originalname.originalextension
    log("Collecting live photos and video files and creating JPEG versions...")

    for i, (id, creationdate, photo, video, selfie) in enumerate(live_idpvs):
        progress(i, len(live_idpvs))

        # assemble filename prefix
        ts = creationdate + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason
        datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')  # TODO maybe convert to system time zone?
        datestring = datestring.replace(" ", "_").replace(":", "-")
        filename_prefix = datestring + "_" + str(id) + "_"
        if selfie:
            filename_prefix = filename_prefix + "selfie_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = TARGET + "/" + filename_prefix + pre + ext.lower()
        #print(targetphoto)
        shutil.copyfile(photo, targetphoto)

        # create jpeg version
        targetjpeg = TARGET + "/" + filename_prefix + pre + ".jpg"
        try:
            subprocess.check_output(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", photo, "--out", targetjpeg])
        except subprocess.CalledProcessError as err:
            sys.exit("sips failed: " + repr(err))

        # copy live video if it exists
        if video is not None:
            targetvideo = TARGET + "/" + filename_prefix + os.path.basename(video)
            #print(targetvideo)
            shutil.copyfile(video, targetvideo)

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
    for i, l in enumerate(instas):
        progress(i, len(instas))

        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]

        photo = absolutepath  # TODO refactor

        # assemble filename prefix  # TODO abstract this
        ts = creationdate + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason
        datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')  # TODO maybe convert to system time zone?
        datestring = datestring.replace(" ", "_").replace(":", "-")
        filename_prefix = datestring + "_" + str(id) + "_" + "instagram" + "_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = TARGET + "/" + filename_prefix + pre + ext.lower()
        shutil.copyfile(photo, targetphoto)

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
    for i, l in enumerate(videos):
        progress(i, len(videos))

        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]
        attachment = l[3]

        video = absolutepath  # TODO refactor

        # assemble filename prefix  # TODO abstract this
        ts = creationdate + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason
        datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')  # TODO maybe convert to system time zone?
        datestring = datestring.replace(" ", "_").replace(":", "-")
        filename_prefix = datestring + "_" + str(id) + "_"
        if attachment:
            filename_prefix = filename_prefix + "slomo_"

        # copy video
        pre, ext = os.path.splitext(os.path.basename(video))
        targetvideo = TARGET + "/" + filename_prefix + pre + ext.lower()
        shutil.copyfile(video, targetvideo)

    # TODO rendered versions of slomos (see resources/media/version)
    # iff hasAttachments = 1? this also catches jpeg screenshots, good
    # join with RKAttachment table but that doesn't help much? reference to AEE/plist files that contain <string>com.apple.video.slomo</string> but nothing more; could try matching based on IMG_... "Creation Date" == fullsizeoutput... "Date Time Original" EXIF data
    # select * from RKMaster where fileName = 'IMG_0027.MOV';
    # 26|KnsExJJpSZeaq84eAnGU+g|AeIEW9HoEl03RoFYWMwASoRoBIfF|1|IMG_0027|567696357.092434|0||0|0|0|0|0|1.72166666666667|559410555|||4964099|720|1280|com.apple.quicktime-movie|EntveYbqTVaNWcxAB%yk+A||IMG_0027|IMG_0027.MOV|0|0|1|0|2018/12/28/20181228-132551/IMG_0027.MOV|559410555|559410555|IMG_0027.MOV|4964099|3|||1|7200|||1||

# TODO bursts
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

    # TODO RKVersion contains column burstPickType indicating which image was chosen as the "hero" image

    log("Collecting burst photos...")
    for i, l in enumerate(bursts):
        progress(i, len(bursts))

        id = l[0]
        absolutepath = MASTERS + "/" + l[1]
        creationdate = l[2]
        burstid = l[3]

        photo = absolutepath  # TODO refactor

        # assemble filename prefix  # TODO abstract this
        ts = creationdate + 977616000 + 691200  # + 31 years + 8 leap days, for whatever reason
        datestring = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')  # TODO maybe convert to system time zone?
        datestring = datestring.replace(" ", "_").replace(":", "-")
        filename_prefix = datestring + "_" + str(id) + "_" + "burst_" + burstid + "_"

        # copy photo
        pre, ext = os.path.splitext(os.path.basename(photo))
        targetphoto = TARGET + "/" + filename_prefix + pre + ext.lower()
        shutil.copyfile(photo, targetphoto)

# TODO hdr images?
# TODO faces into filenames?
# TODO keep a total count of media while collecting and output at the end, or keep stats as dict {insta: 40, etc.}
# TODO RKVersion.selfPortrait = 1 => selfie cam was used, might be handy to include that in filenames


def get_matching_slomos(videos):
    pass

def main():
    create_working_copy_of_photos_db()
    init_target()

    """
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
    """

    #vids = get_live_photo_videos()
    #live_idpvs = get_live_photos(vids)
    #collect_and_convert_live_photos(live_idpvs)

    #get_and_collect_videos()

    get_and_collect_bursts()

    #get_and_collect_insta_photos()

    # TODO stats

if __name__ == "__main__":
    main()

# TODO what to do about screenshots, bursts, whatsapp pics, other random pics, vids, non-live photos etc.?
# TODO prefix with type after date???

# TODO then what to do about pictures left on phone? delete all non-whatsapp ones? all IMG_xxx then, but how?

# https://github.com/xgess/timestamp_all_photos/blob/master/app/apple_photos_library.py
# https://github.com/SummittDweller/merge-photos-faces/blob/master/main.py
