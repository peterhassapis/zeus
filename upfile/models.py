from django.db.models import Model, CharField, DateTimeField, IntegerField
from django.conf import settings
from datetime import datetime
from os import unlink, listdir
from os.path import join as path_join
from random import getrandbits
from time import time
from errno import ENOENT
from fcntl import flock, LOCK_EX, LOCK_UN
import traceback

if hasattr(settings, 'UPFILE_TEMPDIR'):
    UPFILE_TEMPDIR = settings.UPFILE_TEMPDIR
else:
    UPFILE_TEMPDIR = '/tmp/upfile'

class Upfile(Model):
    filename = CharField(max_length=255, primary_key=True)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now_add=True)
    served = IntegerField(default=0, null=False)
    fp = None

    def __init__(self, *args, **kw):
        self.filename = 'up-%x-%x' % (int(time()*1000), getrandbits(32))
        super(Upfile, self).__init__(*args, **kw)

    @classmethod
    def cleanup(cls, updated_before=None, created_before=None):
        entries = listdir(UPFILE_TEMPDIR)
        for filename in entries:
            do_unlink = 0
            try:
                upfile = Upfile.objects.get(filename=filename)
                if ((updated_before is not None
                    and (upfile.updated_at < updated_before))
                    or
                    (created_before is not None
                    and (upfile.created_at < created_before))):
                        do_delete = 1
            except Upfile.DoesNotExist:
                do_delete = 1
                upfile = None

            if do_delete:
                if upfile is not None:
                    upfile.delete()
                try:
                    unlink(path_join(UPFILE_TEMPDIR, filename))
                except:
                    traceback.print_exc()

    def close(self):
        fp = self.fp
        if fp is not None:
            self.fp = None
            fp.close()

    def open_for_reading(self):
        fp = self.fp
        if fp is None:
            try:
                fp = open(path_join(UPFILE_TEMPDIR, self.filename), "r")
                self.fp = fp
            except IOError, e:
                if e.errno == ENOENT:
                    return None
                raise
        return fp

    def open_for_writing(self):
        fp = self.fp
        if fp is None or 'a' not in fp.mode:
            fp = open(path_join(UPFILE_TEMPDIR, self.filename), "a")
            self.fp = fp
        return fp

    def get_size(self):
        fp = self.open_for_reading()
        if fp is None:
            return -1
        fp.seek(0, 2)
        return fp.tell()

    def write(self, offset, data):
        fp = self.open_for_writing()
        fd = fp.fileno()
        flock(fd, LOCK_EX)
        try:
            fp.seek(0, 2)
            size = fp.tell()
            # consider relaxing this check to allow
            # parallel uploading to different parts of the file?
            if size != offset:
                raise ValueError()
            fp.write(data)
            fp.flush()
        finally:
            flock(fd, LOCK_UN)

        self.updated_at = datetime.now()
        self.save()

    def read(self, size=None):
        fp = self.open_for_reading()
        if size is not None:
            return fp.read(size)
        else:
            return fp.read()

    def seek(self, pos, whence=0):
        fp = self.open_for_reading()
        if fp is None:
            return
        fp.seek(pos, whence)

    def unlink(self):
        unlink(path_join(UPFILE_TEMPDIR, self.filename))

