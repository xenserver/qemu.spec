#! /usr/bin/python
import exceptions
import optparse
import os
import re
import subprocess
import sys

def call(args):
    try:
        return subprocess.check_output(args, stderr=subprocess.STDOUT)
    except OSError, (errno, strerror):
        sys.stderr.write("%s: `%s': %s\n" % (sys.argv[0], args[0], strerror))
        sys.exit(2)
    except subprocess.CalledProcessError, (e):
        sys.stderr.write(e.output)
        sys.stderr.write("%s: `%s': returned %d\n" % (sys.argv[0], args[0], e.returncode))
        sys.exit(2)

class version(object):
    version_regex = re.compile("^(\\d+)\\.(\\d+)(\\.(\\d+))?(-rc(\\d+))?$")

    class bad_version(exceptions.Exception):
        pass

    def __init__(self, ver_str):
        self.str = ver_str

        match = self.version_regex.match(self.str)
        if not match:
            raise version.bad_version()

        self.major = int(match.group(1))
        self.minor = int(match.group(2))
        if match.group(4):
            self.micro = int(match.group(4))
        else:
            self.micro = 0
        if match.group(6):
            self.rc = int(match.group(6))
        else:
            self.rc = None

    def __cmp__(self, v):
        if self.major > v.major:
            return 1
        if self.major < v.major:
            return -1
        if self.minor > v.minor:
            return 1
        if self.minor < v.minor:
            return -1
        if self.micro > v.micro:
            return 1
        if self.micro < v.micro:
            return -1

        # major.minor.micro match -- check rc
        if self.rc and not v.rc:
            return -1
        if not self.rc and v.rc:
            return 1
        return cmp(self.rc, v.rc)

    def __str__(self):
        return self.str

    @staticmethod
    def test():
        test_data = [ ( "3.9", "3.9", 0 ),
                      ( "3.9", "3.10", -1 ),
                      ( "3.11", "3.10", 1 ),
                      ( "3.10.25", "3.10.25", 0 ),
                      ( "3.10.25", "3.10.26", -1 ),
                      ( "3.10.25", "3.10.24", 1 ),
                      ( "3.10-rc3", "3.10-rc3", 0 ),
                      ( "3.10-rc3", "3.10-rc4", -1 ),
                      ( "3.10-rc3", "3.10-rc2", 1 ),
                      ( "3.10-rc3", "3.10", -1 ),
                      ( "3.10", "3.10-rc3", 1 ),
                      ( "3.9", "3.10-rc3", -1 ),
                      ( "3.10-rc2", "3.9", 1 ),
                      ( "3.10-rc2", "3.11-rc2", -1 ),
                      ( "3.11-rc2", "3.10-rc2", 1 ),
                      ]


        ok = False
        try:
            v = version("bad")
        except version.bad_version, e:
            ok = True
        if not ok:
            print("no exception")

        for d in test_data:
            a = version(d[0])
            b = version(d[1])
            expected = d[2]

            result = a.__cmp__(b)
            if result != expected:
                print("%s, %s != %d (was %d)" % (a, b, expected, result))

class patch(object):
    def __init__(self, commit, patch_file, ver):
        self.commit = commit
        self.patch_file = patch_file
        self.ver = ver

    def write(self, f):
        if self.ver:
            v = " (%s)" % (self.ver)
        else:
            v = ""
        f.write("# %s%s\n%s\n" % (self.commit, v, self.patch_file))

version.test()

parser = optparse.OptionParser(
    usage = "usage: %prog [option...] <commit ID>...",
    description="Import git commits into a patch queue.")

parser.add_option("-r", "--repo", dest="repo", default=".",
                  help="path to source git repository", metavar="REPO")
parser.add_option("-l", "--list", dest="list",
                  help="file containing a list of commit IDs", metavar="FILE")
(options, args) = parser.parse_args()

commits = []

if options.list:
    list_file = open(options.list, "r")
    for l in list_file.readlines():
        commit = l.strip()
        if commit != "":
            commits.append(commit)

commits.extend(args)

if len(commits) == 0:
    sys.stderr.write("%s: no commits specified\n" % (sys.argv[0]))
    sys.exit(1)

repo = options.repo + "/.git"

patch_base = ".git/patches"
patch_repo = patch_base + "/.git"
patch_dir = patch_base + "/master"

if not os.path.isdir(repo):
    sys.stderr.write("%s: `%s' is not a git repository\n" % (sys.argv[0], options.repo))
    sys.exit(1)

if not os.path.isdir(patch_dir):
    sys.stderr.write("%s: no patch queue present\n" % (sys.argv[0]))
    sys.exit(1)

patches = []

n = 1
for commit in commits:
    print(commit)

    out = call(["git", "--git-dir", repo,
                "format-patch", "--start-number", str(n), "--numbered",
                "--output-directory", patch_dir,
                "%s^!" % commit])
    patch_file = os.path.basename(out.strip())

    tags = []
    earliest_ver = None

    out = call(["git",  "--git-dir", repo,
                "tag", "--contains", commit])
    for tag in out.split("\n"):
        if tag != "" and tag[0] == "v":
            try:
                ver = version(tag[1:])
            except:
                continue
            if not earliest_ver or ver < earliest_ver:
                earliest_ver = ver

    p = patch(commit, patch_file, earliest_ver)
    patches.append(p)

    n += 1

#
# Call git add on all the patch files.
#

for p in patches:
    call(["git", "--git-dir", patch_repo, "--work-tree", patch_base,
          "add", "master/" + p.patch_file])

#
# Add patch files to series file in approximately the right places.
#

series = open(patch_dir + "/series", "r")
series_lines = series.readlines()
series.close()

series = open(patch_dir + "/series.new", "w+")

state = 0
for line in series_lines:
    l = line.strip()
    if l == "# Backports from upstream":
        state = 1
    if state == 1 and l == "":
        for p in patches:
            p.write(series)
        state = 2
    series.write(line)
if state < 2:
    for p in patches:
        p.write(series)

series.close()

os.rename(patch_dir + "/series.new", patch_dir + "/series")
call(["git", "--git-dir", patch_repo, "--work-tree", patch_base,
      "add", "master/series"])
