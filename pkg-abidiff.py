#!/usr/bin/python
#################################################################
# Package ABI Diff 0.97
# Verify API/ABI compatibility of Linux packages (RPM or DEB)
#
# Copyright (C) 2016-2017 Andrey Ponomarenko's ABI Laboratory
#
# Written by Andrey Ponomarenko
#
# PLATFORMS
# =========
#  Linux
#
# REQUIREMENTS
# ============
#  Python 2
#  ABI Compliance Checker (1.99.25 or newer)
#  ABI Dumper (0.99.19 or newer)
#  Universal Ctags
#  GNU Binutils
#  Elfutils
#  G++
#
# This program is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License or
# the GNU Lesser General Public License as published by the Free
# Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License and the GNU Lesser General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.
#################################################################
import argparse
import re
import sys
import os
import tempfile
import shutil
import signal
import subprocess
import traceback
import binascii

TOOL_VERSION = "0.97"

ABI_CC = "abi-compliance-checker"
ABI_DUMPER = "abi-dumper"
CTAGS = "ctags"

ABI_CC_VER = "1.99.25"
ABI_DUMPER_VER = "0.99.19"

PKGS = {}
PKGS_ATTR = {}
FILES = {}
PUBLIC_ABI = False

ARGS = {}
MOD_DIR = None

TMP_DIR = None
TMP_DIR_INT = None

ORIG_DIR = os.getcwd()

CMD_NAME = os.path.basename(__file__)

ERROR_CODE = {"Ok":0, "Error":1, "Empty":10, "NoDebug":11, "NoABI":12}

def init_options():
    global TOOL_VERSION, CMD_NAME
    
    desc = "Check backward API/ABI compatibility of Linux packages (RPM or DEB)"
    parser = argparse.ArgumentParser(description=desc, epilog="example: "+CMD_NAME+" -old P1 P1-DEBUG P1-DEV -new P2 P2-DEBUG P2-DEV")
    
    parser.add_argument('-v', action='version', version='Package ABI Diff (Pkg-ABIdiff) '+TOOL_VERSION)
    parser.add_argument('-old', help='list of old packages (package itself, debug-info and devel package)', nargs='*', metavar='PATH')
    parser.add_argument('-new', help='list of new packages (package itself, debug-info and devel package)', nargs='*', metavar='PATH')
    parser.add_argument('-report-dir', '-o', help='specify a directory to save report (default: ./compat_report)', metavar='DIR')
    parser.add_argument('-dumps-dir', help='specify a directory to save and reuse ABI dumps (default: ./abi_dump)', metavar='DIR')
    parser.add_argument('-bin', help='check binary compatibility only', action='store_true')
    parser.add_argument('-src', help='check source compatibility only', action='store_true')
    parser.add_argument('-rebuild', '-r', help='rebuild ABI dumps and report', action='store_true')
    parser.add_argument('-rebuild-report', help='rebuild report only', action='store_true')
    parser.add_argument('-rebuild-dumps', help='rebuild ABI dumps only', action='store_true')
    parser.add_argument('-quiet', help='do not warn about incompatible build options', action='store_true')
    parser.add_argument('-debug', help='enable debug messages', action='store_true')
    parser.add_argument('-tmp-dir', help='set a directory to store temp files', metavar='DIR')
    parser.add_argument('-ignore-tags', help='optional file with tags to ignore by ctags', metavar='PATH')
    parser.add_argument('-keep-registers-and-offsets', help='dump used registers and stack offsets even if incompatible build options detected', action='store_true')
    parser.add_argument('-use-tu-dump', help='use g++ syntax tree instead of ctags to list symbols in headers', action='store_true')
    parser.add_argument('-include-preamble', help='specify preamble headers (separated by semicolon)', metavar='PATHS')
    parser.add_argument('-include-paths', help='specify include paths (separated by semicolon)', metavar='PATHS')
    
    return parser.parse_args()

def print_err(msg):
    sys.stderr.write(msg+"\n")

def get_modules():
    tool_path = os.path.realpath(__file__)
    tool_dir = os.path.dirname(tool_path)
    
    dirs = [
        tool_dir,
        tool_dir+"/../share/pkg-abidiff"
    ]
    for d in dirs:
        if os.path.exists(d+"/modules"):
            return d+"/modules"
    
    print_err("ERROR: can't find modules")
    s_exit("Error")

def check_cmd(prog):
    for path in os.environ["PATH"].split(os.pathsep):
        path = path.strip('"')
        candidate = path+"/"+prog
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    
    return None

def s_exit(code):
    global TMP_DIR, TMP_DIR_INT, ERROR_CODE
    
    chmod_777(TMP_DIR_INT)
    shutil.rmtree(TMP_DIR_INT)
    
    if not ARGS.tmp_dir:
        shutil.rmtree(TMP_DIR)
    
    sys.exit(ERROR_CODE[code])

def int_exit(signal, frame):
    print "\nGot INT signal"
    print "Exiting"
    s_exit("Error")

def exit_status(code, msg):
    if code!="Ok":
        print_err("ERROR: "+msg)
    else:
        print msg
    
    s_exit(code)

def extract_pkgs(age, kind):
    global PKGS, TMP_DIR_INT
    pkgs = PKGS[age][kind].keys()
    
    extr_dir = TMP_DIR_INT+"/ext/"+age+"/"+kind
    
    if not os.path.exists(extr_dir):
        os.makedirs(extr_dir)
    
    for pkg in pkgs:
        m = re.match(r".*\.(\w+)\Z", os.path.basename(pkg))
        fmt = None
        
        if m:
            fmt = m.group(1)
        
        if not m or fmt not in ["rpm", "deb", "apk", "tbz2", "xpak"]:
            exit_status("Error", "unknown format of package \'"+pkg+"\'")
        
        pkg_abs = os.path.abspath(pkg)
        
        os.chdir(extr_dir)
        if fmt=="rpm":
            subprocess.call("rpm2cpio \""+pkg_abs+"\" | cpio -id --quiet", shell=True)
        elif fmt=="deb":
            subprocess.call(["dpkg-deb", "--extract", pkg_abs, "."])
        elif fmt=="apk":
            with open(TMP_DIR_INT+"/err", "a") as err_log:
                subprocess.call(["tar", "-xf", pkg_abs], stderr=err_log)
        elif fmt in ("tbz2", "xpak"):
            # note: this needs tar that detects compression algo
            subprocess.call(["tar", "-xf", pkg_abs])
        os.chdir(ORIG_DIR)
    
    return extr_dir

def get_rel_path(path):
    global TMP_DIR_INT
    path = path.replace(TMP_DIR_INT+"/", "")
    path = re.sub(r"\Aext/(old|new)/(rel|debug|devel)/", "", path)
    return path

def is_object(path):
    name = os.path.basename(path)
    if re.search(r"lib.*\.so(\..+|\Z)", name):
        if read_bytes(path)=="7f454c46":
            return True
    return False

def is_header(name):
    if re.search(r"\.(h|hh|hp|hxx|hpp|h\+\+|tcc)\Z", name):
        return True
    
    return False

def get_fmt(path):
    m = re.match(r".*\.([^\.]+)\Z", path)
    if m:
        return m.group(1)
    
    return None

def get_attrs(path):
    fmt = get_fmt(path)
    
    name = None
    ver = None
    rl = None
    arch = None
    
    if fmt=="rpm":
        r = subprocess.check_output(["rpm", "-qp", "--queryformat", "%{name},%{version},%{release},%{arch}", path])
        name, ver, rl, arch = r.split(",")
        ver = ver+"-"+rl
    elif fmt=="deb":
        r = subprocess.check_output(["dpkg", "-f", path])
        attr = {"Package":None, "Version":None, "Architecture":None}
        for line in r.split("\n"):
            m = re.match(r"(\w+)\s*:\s*(.+)", line)
            if m:
                attr[m.group(1)] = m.group(2)
        
        name = attr["Package"]
        ver = attr["Version"]
        arch = attr["Architecture"]
    elif fmt=="apk":
        with open(TMP_DIR_INT+"/err", "a") as err_log:
            r = subprocess.check_output(["tar", "-xf", path, ".PKGINFO", "-O"], stderr=err_log)
        
        attr = {}
        
        for line in r.split("\n"):
            m = re.match(r"(\w+)\s*=\s*(.+)", line)
            if m:
                attr[m.group(1)] = m.group(2)
        
        name = attr["pkgname"]
        ver = attr["pkgver"]
        arch = attr["arch"]
    elif fmt in ("tbz2", "xpak"):
        # no command-line tools to extract that metadata
        import portage.versions
        import portage.xpak

        xpak = portage.xpak.tbz2(path).get_data()
        namever = xpak["CATEGORY"].strip()+"/"+xpak["PF"].strip()
        name = portage.versions.cpv_getkey(namever)
        ver = portage.versions.cpv_getversion(namever)
        # not strictly an arch but meaningful enough
        arch = xpak["CHOST"].strip()
    
    if name is not None and ver is not None and arch is not None:
        return [name, ver, arch]
    
    return None

def get_soname(path):
    r = subprocess.check_output(["objdump", "-p", path])
    m = re.search(r"SONAME\s+([^ ]+)", r)
    if m:
        return m.group(1).rstrip()
    
    return None

def get_short_name(obj):
    m = re.match(r"(.+\.so)(\..+|\Z)", obj)
    if m:
        return m.group(1)
    
    return None

def get_shortest_name(obj):
    m = re.match(r"\A([^\d\.]+)", obj)
    if m:
        return m.group(1)
    
    return None

def read_file(path):
    f = open(path, 'r')
    content = f.read()
    f.close()
    return content

def read_line(path):
    f = open(path, 'r')
    content = f.readline()
    f.close()
    return content

def write_file(path, content):
    f = open(path, 'w')
    f.write(content)
    f.close()

def read_stat(path, rdir):
    stat = {}
    line = read_line(path)
    for e in line.split(";"):
        m = re.search(r"(\w+):([^\s]+)", e)
        if m:
            stat[m.group(1)] = m.group(2)
    
    total = 0
    for k in stat:
        if k.find("_problems_")!=-1 or k=="changed_constants":
            total += int(stat[k])
    
    stat["total"] = total
    
    rpath = path.replace(rdir+"/", "")
    stat["path"] = rpath
    
    return stat

def compose_html_head(title, keywords, description):
    styles = read_file(MOD_DIR+"/Internals/Styles/Report.css")
    
    cnt =  "<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\" \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n"
    cnt += "<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">\n"
    cnt += "<head>\n"
    cnt += "<meta http-equiv=\"Content-Type\" content=\"text/html; charset=utf-8\" />\n"
    cnt += "<meta name=\"keywords\" content=\""+keywords+"\" />\n"
    cnt += "<meta name=\"description\" content=\""+description+"\" />\n\n"

    cnt += "<title>\n"
    cnt += "    "+title+"\n"
    cnt += "</title>\n\n"
    
    cnt += "<style type=\"text/css\">\n"
    cnt += styles
    cnt += "</style>\n"

    cnt += "</head>\n"
    
    return cnt

def get_bc_class(rate, total):
    cclass = "ok"
    if float(rate)==100:
        if total:
            cclass = "warning"
    else:
        if float(rate)>=90:
            cclass = "warning"
        elif float(rate)>=80:
            cclass = "almost_compatible"
        else:
            cclass = "incompatible"
    
    return cclass

def format_num(num):
    num = re.sub(r"\A(\d+\.\d\d).*\Z", r"\1", str(num))
    num = re.sub(r"(\.\d)0\Z", r"\1", num)
    num = re.sub(r"\A(\d+)\.0\Z", r"\1", num)
    return num

def get_dumpversion(prog):
    ver = subprocess.check_output([prog, "-dumpversion"])
    return ver.rstrip()

def get_version(prog):
    ver = subprocess.check_output([prog, "--version"])
    return ver.rstrip()

def cmp_vers(x, y):
    xp = x.split(".")
    yp = y.split(".")
    
    for k in range(len(xp), max(len(xp), len(yp))):
        xp.append("0")
    
    for k in range(len(yp), max(len(xp), len(yp))):
        yp.append("0")
    
    for k in range(0, len(xp)):
        a = xp[k]
        b = yp[k]
        
        if a==b:
            continue
        
        if(int(a) > int(b)):
            return 1
        else:
            return -1
    
    return 0

def get_dump_attr(path):
    attr = {}
    attr["empty"] = False
    attr["lang"] = None
    f = open(path, 'r')
    for line in f:
        if line.find("'Language' =>")!=-1:
            m = re.search(r"'Language' => '(.+)'", line)
            if m:
                attr["lang"] = m.group(1)
        elif line.find("'SymbolInfo' =>")!=-1:
            attr["empty"] = (line.find("'SymbolInfo' => {}")!=-1)
            break
    
    f.close()
    return attr

def count_symbols(path, obj, age):
    global ABI_CC
    print "Counting symbols in the ABI dump for "+os.path.basename(obj)+" ("+age+")"
    count = subprocess.check_output([ABI_CC, "-count-symbols", path])
    return int(count.rstrip())

def read_bytes(path):
    fp = open(path, 'rb')
    buf = fp.read()
    fp.close()
    return binascii.b2a_hex(buf[0:4])

def chmod_777(path):
    subprocess.call(["chmod", "777", "-R", path])

def scenario():
    signal.signal(signal.SIGINT, int_exit)
    
    global MOD_DIR
    MOD_DIR = get_modules()
    
    global ARGS
    ARGS = init_options()
    
    global TMP_DIR, TMP_DIR_INT
    if ARGS.tmp_dir:
        TMP_DIR = ARGS.tmp_dir
    else:
        TMP_DIR = tempfile.mkdtemp()
    
    TMP_DIR_INT = TMP_DIR+"/PKG_ABIDIFF_TMP"
    if not os.path.exists(TMP_DIR_INT):
        os.makedirs(TMP_DIR_INT)
    
    if not ARGS.old:
        exit_status("Error", "old packages are not specified (-old option)")
    
    if not ARGS.new:
        exit_status("Error", "new packages are not specified (-new option)")
    
    global ABI_CC, ABI_DUMPER, CTAGS
    
    if not check_cmd(ABI_CC):
        exit_status("Error", "ABI Compliance Checker "+ABI_CC_VER+" or newer is not installed")
    
    if cmp_vers(get_dumpversion(ABI_CC), ABI_CC_VER)<0:
        exit_status("Error", "the version of ABI Compliance Checker should be "+ABI_CC_VER+" or newer")
    
    if not check_cmd(ABI_DUMPER):
        exit_status("Error", "ABI Dumper "+ABI_DUMPER_VER+" or newer is not installed")
    
    if cmp_vers(get_dumpversion(ABI_DUMPER), ABI_DUMPER_VER)<0:
        exit_status("Error", "the version of ABI Dumper should be "+ABI_DUMPER_VER+" or newer")
    
    if not ARGS.bin and not ARGS.src:
        ARGS.bin = True
        ARGS.src = True
    
    if ARGS.rebuild:
        ARGS.rebuild_dumps = True
        ARGS.rebuild_report = True
    
    LIST = {}
    LIST["old"] = ARGS.old
    LIST["new"] = ARGS.new
    
    global PKGS
    PKGS["old"] = {}
    PKGS["new"] = {}
    
    global PKGS_ATTR
    PKGS_ATTR["old"] = {}
    PKGS_ATTR["new"] = {}
    
    pkg_formats = {}
    for age in ["old", "new"]:
        for pkg in LIST[age]:
            if not os.path.exists(pkg):
                exit_status("Error", "can't access '"+pkg+"'")
            
            if not os.path.isfile(pkg):
                exit_status("Error", "input argument is not a package")
            
            fmt = get_fmt(pkg)
            
            if fmt is None or fmt not in ["rpm", "deb", "apk", "tbz2", "xpak"]:
                exit_status("Error", "unknown format of package "+pkg)
            
            pkg_formats[fmt] = 1
    
    if "rpm" in pkg_formats:
        if not check_cmd("rpm"):
            exit_status("Error", "can't find RPM package manager")
        if not check_cmd("rpm2cpio"):
            exit_status("Error", "can't find rpm2cpio")
    
    if "deb" in pkg_formats:
        if not check_cmd("dpkg"):
            exit_status("Error", "can't find dpkg")

    if "tbz2" in pkg_formats or "xpak" in pkg_formats:
        try:
            import portage.xpak
        except ImportError:
            exit_status("Error", "can't find Portage modules")
    
    for age in ["old", "new"]:
        pname = {}
        pver = {}
        parch = {}
        for pkg in LIST[age]:
            fname = os.path.basename(pkg)
            kind = "rel"
            
            if re.match(r".*-(headers-|devel-|dev-|dev_).*", fname):
                kind = "devel"
            elif re.match(r".*-(debuginfo-|dbg[_\-]|dbgsym_).*", fname):
                kind = "debug"
            
            if kind in PKGS[age]:
                if kind=="rel":
                    exit_status("Error", "only one release package can be specified ("+age+")")
                elif kind=="debug":
                    exit_status("Error", "only one debug package can be specified ("+age+")")
            else:
                PKGS[age][kind] = {}
            
            PKGS[age][kind][pkg] = 1
            
            attrs = get_attrs(pkg)
            if attrs:
                pname[kind] = attrs[0]
                
                if kind in pver:
                    if pver[kind]!=attrs[1]:
                        exit_status("Error", "different versions of "+kind+" packages ("+age+")")
                else:
                    pver[kind] = attrs[1]
                
                if kind in parch:
                    if parch[kind]!=attrs[2]:
                        exit_status("Error", "different architectures of "+kind+" packages ("+age+")")
                else:
                    parch[kind] = attrs[2]
            else:
                exit_status("Error", "can't read attributes of a package "+pkg)
        
        if "rel" not in PKGS[age]:
            exit_status("Error", age+" release package is not specified ("+age+")")
        
        if "debug" not in PKGS[age]:
            exit_status("Error", age+" debuginfo package is not specified ("+age+")")
        
        if pver["rel"]!=pver["debug"]:
            exit_status("Error", "different versions of packages ("+age+")")
        
        if "devel" in pver:
            if pver["rel"]!=pver["devel"]:
                exit_status("Error", "different versions of packages ("+age+")")
        
        if parch["rel"]!=parch["debug"]:
            exit_status("Error", "different architectures of packages ("+age+")")
        
        if "devel" in parch:
            if parch["rel"]!=parch["devel"]:
                exit_status("Error", "different architectures of packages ("+age+")")
        
        PKGS_ATTR[age]["name"] = pname["rel"]
        PKGS_ATTR[age]["ver"] = pver["rel"]
        PKGS_ATTR[age]["arch"] = parch["rel"]
    
    if PKGS_ATTR["old"]["name"]!=PKGS_ATTR["new"]["name"]:
        print "WARNING: different names of old and new packages"
    
    if PKGS_ATTR["old"]["arch"]!=PKGS_ATTR["new"]["arch"]:
        exit_status("Error", "different architectures of old and new packages")
    
    global PUBLIC_ABI
    if "devel" in PKGS["old"]:
        if "devel" in PKGS["new"]:
            PUBLIC_ABI = True
            if len(PKGS["old"]["devel"].keys())!=len(PKGS["new"]["devel"].keys()):
                exit_status("Error", "different number of old and new devel packages")
        else:
            exit_status("Error", "new devel package is not specified")
    elif "devel" in PKGS["new"]:
        exit_status("Error", "old devel package is not specified")
    else:
        print "WARNING: devel packages are not specified, can't filter public ABI"
    
    if PUBLIC_ABI:
        if not check_cmd(CTAGS):
            exit_status("Error", "Universal Ctags program is not installed")
        
        ctags_ver = get_version(CTAGS)
        if ctags_ver.lower().find("universal")==-1:
            exit_status("Error", "requires Universal Ctags")
    
    print "Extracting packages ..."
    global FILES
    FILES["old"] = {}
    FILES["new"] = {}
    
    e_dir = {}
    e_dir["old"] = {}
    e_dir["new"] = {}
    
    for age in ["old", "new"]:
        for kind in ["rel", "debug", "devel"]:
            if kind not in PKGS[age]:
                continue
            
            e_dir[age][kind] = extract_pkgs(age, kind)
            for root, dirs, files in os.walk(e_dir[age][kind]):
                for f in files:
                    fpath = root+"/"+f
                    
                    if os.path.islink(fpath):
                        continue
                    
                    fkind = None
                    if kind=="rel":
                        if is_object(fpath):
                            fkind = "object"
                    elif kind=="debug":
                        if re.match(r".*\.debug\Z", f):
                            fkind = "debuginfo"
                        
                        if get_fmt(PKGS[age]["debug"].keys()[0])=="deb":
                            if is_object(fpath):
                                fkind = "debuginfo"
                    elif kind=="devel":
                        if fpath.find("/include/")!=-1 or is_header(f):
                            fkind = "header"
                    
                    if fkind:
                        if fkind not in FILES[age]:
                            FILES[age][fkind] = {}
                        FILES[age][fkind][fpath] = 1
                    
                    if kind not in FILES[age]:
                        FILES[age][kind] = {}
                    
                    FILES[age][kind][fpath] = 1
    
    abi_dump = {}
    soname = {}
    short_name = {}
    shortest_name = {}
    
    for age in ["old", "new"]:
        print "Creating ABI dumps ("+age+") ..."
        if "debuginfo" not in FILES[age]:
            exit_status("NoDebug", "debuginfo files are not found in "+age+" debuginfo package")
        
        if "object" not in FILES[age]:
            exit_status("NoABI", "shared objects are not found in "+age+" release package")
        
        objects = FILES[age]["object"].keys()
        objects.sort(key=lambda x: x.lower())
        
        abi_dump[age] = {}
        soname[age] = {}
        short_name[age] = {}
        shortest_name[age] = {}
        
        parch = PKGS_ATTR[age]["arch"]
        pname = PKGS_ATTR[age]["name"]
        pver = PKGS_ATTR[age]["ver"]
        
        dump_dir = "abi_dump"
        if ARGS.dumps_dir:
            dump_dir = ARGS.dumps_dir
        
        dump_dir += "/"+parch+"/"+pname+"/"+pver
        print "Using dumps directory: "+dump_dir
        
        for obj in objects:
            oname = os.path.basename(obj)
            
            soname[age][oname] = get_soname(obj)
            short_name[age][oname] = get_short_name(oname)
            shortest_name[age][oname] = get_shortest_name(oname)
            
            obj_dump_path = dump_dir+"/"+oname+"/ABI.dump"
            
            if os.path.exists(obj_dump_path):
                if ARGS.rebuild_dumps:
                    os.remove(obj_dump_path)
                else:
                    print "Using existing ABI dump for "+oname
                    abi_dump[age][oname] = obj_dump_path
                    continue
            
            print "Creating ABI dump for "+oname
            
            cmd_d = [ABI_DUMPER, "-o", obj_dump_path, "-lver", pver]
            
            if ARGS.quiet:
                cmd_d.append("-quiet")
            
            cmd_d.append("-search-debuginfo")
            cmd_d.append(e_dir[age]["debug"])
            
            if PUBLIC_ABI:
                if "header" in FILES[age]:
                    cmd_d.append("-public-headers")
                    cmd_d.append(e_dir[age]["devel"])
            
            if ARGS.use_tu_dump:
                cmd_d.append("-use-tu-dump")
                if ARGS.include_preamble:
                    cmd_d.append("-include-preamble")
                    cmd_d.append(ARGS.include_preamble)
                if ARGS.include_paths:
                    cmd_d.append("-include-paths")
                    cmd_d.append(ARGS.include_paths)
            elif ARGS.ignore_tags:
                cmd_d.append("-ignore-tags")
                cmd_d.append(ARGS.ignore_tags)
            
            if ARGS.keep_registers_and_offsets:
                cmd_d.append("-keep-registers-and-offsets")
            
            cmd_d.append(obj)
            
            if ARGS.debug:
                print "Executing "+" ".join(cmd_d)
            
            ecode = 0
            
            with open(TMP_DIR_INT+"/log", "a") as log:
                ecode = subprocess.call(cmd_d, stdout=log)
            
            if not os.path.exists(obj_dump_path):
                if ecode==12:
                    continue
                else:
                    exit_status("Error", "failed to create ABI dump for object "+oname+" ("+age+")")
            
            dump_attr = get_dump_attr(obj_dump_path)
            
            if dump_attr["empty"]:
                print "WARNING: empty ABI dump for "+oname+" ("+age+")"
                os.remove(obj_dump_path)
            elif dump_attr["lang"] not in ["C", "C++"]:
                print "WARNING: unsupported language "+dump_attr["lang"]+" of "+oname+" ("+age+")"
                os.remove(obj_dump_path)
            else:
                abi_dump[age][oname] = obj_dump_path
        
    print "Comparing ABIs ..."
    soname_r = {}
    short_name_r = {}
    shortest_name_r = {}
    
    for age in ["old", "new"]:
        soname_r[age] = {}
        for obj in soname[age]:
            sname = soname[age][obj]
            if sname not in soname_r[age]:
                soname_r[age][sname] = {}
            soname_r[age][sname][obj] = 1
        
        short_name_r[age] = {}
        for obj in short_name[age]:
            shname = short_name[age][obj]
            if shname not in short_name_r[age]:
                short_name_r[age][shname] = {}
            short_name_r[age][shname][obj] = 1
        
        shortest_name_r[age] = {}
        for obj in shortest_name[age]:
            shname = shortest_name[age][obj]
            if shname not in shortest_name_r[age]:
                shortest_name_r[age][shname] = {}
            shortest_name_r[age][shname][obj] = 1
    
    old_objects = abi_dump["old"].keys()
    new_objects = abi_dump["new"].keys()
    
    if objects and not old_objects:
        exit_status("Empty", "all ABI dumps are empty or invalid")
    
    old_objects.sort(key=lambda x: x.lower())
    new_objects.sort(key=lambda x: x.lower())
    
    mapped = {}
    mapped_r = {}
    removed = {}
    
    report_dir = None
    if ARGS.report_dir:
        report_dir = ARGS.report_dir
    else:
        report_dir = "compat_report"
        report_dir += "/"+PKGS_ATTR["old"]["arch"]+"/"+PKGS_ATTR["old"]["name"]
        report_dir += "/"+PKGS_ATTR["old"]["ver"]+"/"+PKGS_ATTR["new"]["ver"]
    
    if os.path.exists(report_dir):
        if ARGS.rebuild_report:
            if os.path.exists(report_dir+"/index.html"):
                os.remove(report_dir+"/index.html")
        else:
            exit_status("Ok", "The report already exists: "+report_dir)
    else:
        os.makedirs(report_dir)
    
    compat = {}
    renamed_object = {}
    for obj in old_objects:
        new_obj = None
        
        # match by SONAME
        if obj in soname["old"]:
            sname = soname["old"][obj]
            if sname in soname_r["new"]:
                bysoname = soname_r["new"][sname].keys()
                if bysoname and len(bysoname)==1:
                    new_obj = bysoname[0]
        
        # match by name
        if new_obj is None:
            if obj in new_objects:
                new_obj = obj
        
        # match by short name
        if new_obj is None:
            if obj in short_name["old"]:
                shname = short_name["old"][obj]
                if shname in short_name_r["new"]:
                    byshort = short_name_r["new"][shname].keys()
                    if byshort and len(byshort)==1:
                        new_obj = byshort[0]
        
        # match by shortest name
        if new_obj is None:
            if obj in shortest_name["old"]:
                shname = shortest_name["old"][obj]
                if shname in shortest_name_r["new"]:
                    byshort = shortest_name_r["new"][shname].keys()
                    if byshort and len(byshort)==1:
                        new_obj = byshort[0]
        
        if new_obj is None:
            removed[obj] = 1
            continue
        
        mapped[obj] = new_obj
        mapped_r[new_obj] = obj
    
    added = {}
    for obj in new_objects:
        if obj not in mapped_r:
            added[obj] = 1
    
    # one object
    if not mapped:
        if len(old_objects)==1 and len(new_objects)==1:
            obj = old_objects[0]
            new_obj = new_objects[0]
            
            mapped[obj] = new_obj
            renamed_object[obj] = new_obj
            
            removed.pop(obj, None)
            added.pop(new_obj, None)
    
    mapped_objs = mapped.keys()
    mapped_objs.sort(key=lambda x: x.lower())
    for obj in mapped_objs:
        new_obj = mapped[obj]
        
        if obj not in abi_dump["old"]:
            continue
        
        if new_obj not in abi_dump["new"]:
            continue
        
        print "Comparing "+obj+" (old) and "+new_obj+" (new)"
        
        obj_report_dir = report_dir+"/"+obj
        
        if os.path.exists(obj_report_dir):
            shutil.rmtree(obj_report_dir)
        
        bin_report = obj_report_dir+"/abi_compat_report.html"
        src_report = obj_report_dir+"/src_compat_report.html"
        
        cmd_c = [ABI_CC, "-l", obj, "-component", "object"]
        
        if ARGS.bin:
            cmd_c.append("-bin")
            cmd_c.extend(["-bin-report-path", bin_report])
        if ARGS.src:
            cmd_c.append("-src")
            cmd_c.extend(["-src-report-path", src_report])
        
        cmd_c.append("-old")
        cmd_c.append(abi_dump["old"][obj])
        
        cmd_c.append("-new")
        cmd_c.append(abi_dump["new"][new_obj])
        
        if ARGS.debug:
            print "Executing "+" ".join(cmd_c)
        
        with open(TMP_DIR_INT+"/log", "w") as log:
            subprocess.call(cmd_c, stdout=log)
        
        if ARGS.bin:
            if not os.path.exists(bin_report):
                print_err("ERROR: failed to create BC report for object "+obj)
                continue
        
        if ARGS.src:
            if not os.path.exists(src_report):
                print_err("ERROR: failed to create SC report for object "+obj)
                continue
        
        compat[obj] = {}
        res = []
        
        if ARGS.bin:
            compat[obj]["bin"] = read_stat(bin_report, report_dir)
            res.append("BC: "+format_num(100-float(compat[obj]["bin"]["affected"]))+"%")
        
        if ARGS.src:
            compat[obj]["src"] = read_stat(src_report, report_dir)
            res.append("SC: "+format_num(100-float(compat[obj]["src"]["affected"]))+"%")
        
        print ", ".join(res)
    
    if mapped_objs and not compat:
        exit_status("Error", "failed to create reports for objects")
    
    object_symbols = {}
    changed_soname = {}
    for obj in mapped:
        new_obj = mapped[obj]
        
        old_soname = soname["old"][obj]
        new_soname = soname["new"][new_obj]
        
        if old_soname and new_soname and old_soname!=new_soname:
            changed_soname[obj] = new_soname
    
    # JSON report
    affected_t = 0
    problems_t = 0
    
    affected_t_eff = 0
    
    added_t = 0
    removed_t = 0
    
    affected_t_src = 0
    problems_t_src = 0
    
    total_funcs = 0
    
    for obj in compat:
        if ARGS.bin:
            report = compat[obj]["bin"]
        else:
            report = compat[obj]["src"]
        
        old_dump = abi_dump["old"][obj]
        funcs = count_symbols(old_dump, obj, "old")
        object_symbols[obj] = funcs
        
        affected_t_delta = float(report["affected"])*funcs
        
        affected_t += affected_t_delta
        problems_t += int(report["total"])
        
        if obj in changed_soname:
            affected_t_eff += 100*funcs
        else:
            affected_t_eff += affected_t_delta
        
        added_t += int(report["added"])
        removed_t += int(report["removed"])
        
        if ARGS.src:
            report_src = compat[obj]["src"]
            affected_t_src += float(report_src["affected"])*funcs
            problems_t_src += int(report_src["total"])
        
        total_funcs += funcs
    
    removed_by_objects_t = 0
    
    for obj in removed:
        old_dump = abi_dump["old"][obj]
        removed_by_objects_t += count_symbols(old_dump, obj, "old")
    
    bc = 100
    bc_eff = 100
    
    if total_funcs:
        bc -= affected_t/total_funcs
        bc_eff -= affected_t_eff/total_funcs
    
    if ARGS.src:
        bc_src = 100
        if total_funcs:
            bc_src -= affected_t_src/total_funcs
    
    if old_objects and removed:
        delta = (1-(removed_by_objects_t/(total_funcs+removed_by_objects_t)))
        bc *= delta
        if ARGS.src:
            bc_src *= delta
    
    bc = format_num(bc)
    bc_eff = format_num(bc_eff)
    
    if ARGS.src:
        bc_src = format_num(bc_src)
    
    meta = []
    if ARGS.bin:
        meta.append("\"BC\": "+str(bc))
        meta.append("\"BC_Effective\": "+str(bc_eff))
    if ARGS.src:
        meta.append("\"Source_BC\": "+str(bc_src))
    meta.append("\"Added\": "+str(added_t))
    meta.append("\"Removed\": "+str(removed_t))
    if ARGS.bin:
        meta.append("\"TotalProblems\": "+str(problems_t))
    if ARGS.src:
        meta.append("\"Source_TotalProblems\": "+str(problems_t_src))
    meta.append("\"ObjectsAdded\": "+str(len(added)))
    meta.append("\"ObjectsRemoved\": "+str(len(removed)))
    meta.append("\"ChangedSoname\": "+str(len(changed_soname)))
    
    write_file(report_dir+"/meta.json", "{\n  "+",\n  ".join(meta)+"\n}\n")
    
    # HTML report
    n1 = PKGS_ATTR["old"]["name"]
    n2 = PKGS_ATTR["new"]["name"]
    
    v1 = PKGS_ATTR["old"]["ver"]
    v2 = PKGS_ATTR["new"]["ver"]
    
    arch = PKGS_ATTR["old"]["arch"]
    
    report = "<h1>ABI report"
    if n1==n2:
        title = n1+": API/ABI report between "+v1+" and "+v2+" versions"
        keywords = n1+", API, ABI, changes, compatibility, report"
        desc = "API/ABI compatibility report between "+v1+" and "+v2+" versions of the "+n1
        report += " for "+n1+": <u>"+v1+"</u> vs <u>"+v2+"</u>"
    else:
        title = "API/ABI report between "+n1+"-"+v1+" and "+n2+"-"+v2+" packages"
        keywords = n1+", "+n2+", API, ABI, changes, compatibility, report"
        desc = "API/ABI compatibility report between "+n1+"-"+v1+" and "+n2+"-"+v2+" packages"
        report += " for <u>"+n1+"-"+v1+"</u> vs <u>"+n2+"-"+v2+"</u>"
    
    if not ARGS.bin:
        report += " (source compatibility)"
    
    report += "</h1>\n"
    
    report += "<h2>Test Info</h2>\n"
    report += "<table class='summary'>\n"
    report += "<tr>\n"
    report += "<th class='left'>Package</th><td class='right'>"+n1+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<th class='left'>Old Version</th><td class='right'>"+v1+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<th class='left'>New Version</th><td class='right'>"+v2+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<th class='left'>Arch</th><td class='right'>"+arch+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    if PUBLIC_ABI:
        report += "<th class='left'>Subject</th><td class='right'>Public ABI</td>\n"
    else:
        report += "<th class='left'>Subject</th><td class='right'>Public ABI +<br/>Private ABI</td>\n"
    report += "</tr>\n"
    report += "</table>\n"
    
    report += "<h2>Test Result</h2>\n"
    report += "<span class='result'>\n"
    if ARGS.bin:
        report += "Binary compatibility: <span class='"+get_bc_class(bc_eff, problems_t)+"' title='Avg. binary compatibility rate'>"+bc_eff+"%</span>\n"
        if changed_soname.keys():
            report += " (<span class='incompatible' title='Effective binary compatibility is "+bc_eff+"%"+" due to changed SONAME'>changed SONAME</span>)"
        report += "<br/>\n"
    
    if ARGS.src:
        report += "Source compatibility: <span class='"+get_bc_class(bc_src, problems_t_src)+"' title='Avg. source compatibility rate'>"+bc_src+"%</span>\n"
        report += "<br/>\n"
    
    report += "</span>\n"
    
    report += "<h2>Packages</h2>\n"
    report += "<table class='summary'>\n"
    report += "<tr>\n"
    report += "<th>Old</th><th>New</th><th title='*.so, *.debug and header files'>Files</th>\n"
    report += "</tr>\n"
    
    target = {}
    target["rel"] = "object"
    target["debug"] = "debuginfo"
    target["devel"] = "header"
    
    for kind in ["rel", "debug", "devel"]:
        if kind=="devel" and not PUBLIC_ABI:
            continue
        
        pkgs1 = PKGS["old"][kind].keys()
        pkgs2 = PKGS["new"][kind].keys()
        
        pkgs1.sort(key=lambda x: x.lower())
        pkgs2.sort(key=lambda x: x.lower())
        
        total = len(pkgs1)
        
        pfiles = False
        
        for i in range(0, total):
            report += "<tr>\n"
            report += "<td class='object'>"+os.path.basename(pkgs1[i])+"</td>\n"
            report += "<td class='object'>"+os.path.basename(pkgs2[i])+"</td>\n"
            if not pfiles:
                if total>1:
                    report += "<td class='center' rowspan='"+str(total)+"'>"
                else:
                    report += "<td class='center'>"
                if target[kind] in FILES["old"]:
                    report += str(len(FILES["old"][target[kind]]))
                else:
                    report += "0"
                report += "</td>\n"
                pfiles = True
            report += "</tr>\n"
    
    report += "</table>\n"
    
    report += "<h2>Shared Objects</h2>\n"
    report += "<table class='summary'>\n"
    
    cols = 5
    if ARGS.bin and ARGS.src:
        report += "<tr>\n"
        report += "<th rowspan='2'>Object</th>\n"
        report += "<th colspan='2'>Compatibility</th>\n"
        report += "<th rowspan='2'>Added<br/>Symbols</th>\n"
        report += "<th rowspan='2'>Removed<br/>Symbols</th>\n"
        report += "<th rowspan='2'>Total<br/>Symbols</th>\n"
        report += "</tr>\n"
        
        report += "<tr>\n"
        report += "<th title='Binary compatibility'>BC</th>\n"
        report += "<th title='Source compatibility'>SC</th>\n"
        report += "</tr>\n"
    else:
        cols -= 1
        report += "<tr>\n"
        report += "<th>Object</th>\n"
        
        if ARGS.bin:
            report += "<th>Binary<br/>Compatibility</th>\n"
        else:
            report += "<th>Source<br/>Compatibility</th>\n"
        
        report += "<th>Added<br/>Symbols</th>\n"
        report += "<th>Removed<br/>Symbols</th>\n"
        report += "<th>Total<br/>Symbols</th>\n"
        report += "</tr>\n"
    
    for obj in new_objects:
        if obj in added:
            report += "<tr>\n"
            report += "<td class='object'>"+obj+"</td>\n"
            report += "<td colspan=\'"+str(cols)+"\' class='added'>Added to package</td>\n"
            report += "</tr>\n"
    
    for obj in old_objects:
        report += "<tr>\n"
        
        name = obj
        
        if obj in mapped:
            if obj in changed_soname:
                name += "<br/>"
                name += "<br/>"
                name += "<span class='incompatible'>(changed SONAME from<br/>\""+soname["old"][obj]+"\"<br/>to<br/>\""+changed_soname[obj]+"\")</span>"
            elif obj in renamed_object:
                name += "<br/>"
                name += "<br/>"
                name += "<span class='incompatible'>(changed file name from<br/>\""+obj+"\"<br/>to<br/>\""+renamed_object[obj]+"\")</span>"
        
        report += "<td class='object'>"+name+"</td>\n"
        
        if obj in mapped:
            if obj not in compat:
                for i in range(0, cols):
                    report += "<td>N/A</td>\n"
                continue
            
            if ARGS.bin:
                rate = 100 - float(compat[obj]["bin"]["affected"])
                added_symbols = compat[obj]["bin"]["added"]
                removed_symbols = compat[obj]["bin"]["removed"]
                total = compat[obj]["bin"]["total"]
                cclass = get_bc_class(rate, total)
                rpath = compat[obj]["bin"]["path"]
            
            if ARGS.src:
                rate_src = 100 - float(compat[obj]["src"]["affected"])
                added_symbols_src = compat[obj]["src"]["added"]
                removed_symbols_src = compat[obj]["src"]["removed"]
                total_src = compat[obj]["src"]["total"]
                cclass_src = get_bc_class(rate_src, total_src)
                rpath_src = compat[obj]["src"]["path"]
            
            if ARGS.bin:
                report += "<td class=\'"+cclass+"\'>"
                report += "<a href='"+rpath+"'>"+format_num(rate)+"%</a>"
                report += "</td>\n"
            
            if ARGS.src:
                report += "<td class=\'"+cclass_src+"\'>"
                report += "<a href='"+rpath_src+"'>"+format_num(rate_src)+"%</a>"
                report += "</td>\n"
            
            if not ARGS.bin:
                if int(added_symbols_src)>0:
                    report += "<td class='added'><a class='num' href='"+rpath_src+"#Added'>"+added_symbols_src+" new</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
                
                if int(removed_symbols_src)>0:
                    report += "<td class='removed'><a class='num' href='"+rpath_src+"#Removed'>"+removed_symbols_src+" removed</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
            else:
                if int(added_symbols)>0:
                    report += "<td class='added'><a class='num' href='"+rpath+"#Added'>"+added_symbols+" new</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
                
                if int(removed_symbols)>0:
                    report += "<td class='removed'><a class='num' href='"+rpath+"#Removed'>"+removed_symbols+" removed</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
            
            report += "<td>"+str(object_symbols[obj])+"</td>\n"
        elif obj in removed:
            report += "<td colspan=\'"+str(cols)+"\' class='removed'>Removed from package</td>\n"
        
        report += "</tr>\n"
    
    report += "</table>\n"
    
    report += "<br/>\n"
    report += "<br/>\n"
    
    report += "<hr/>\n"
    report += "<div class='footer' align='right'><i>Generated by <a href='https://github.com/lvc/pkg-abidiff'>Package ABI Diff</a> "+TOOL_VERSION+" &#160;</i></div>\n"
    report += "<br/>\n"
    
    report = compose_html_head(title, keywords, desc)+"<body>\n"+report+"\n</body>\n</html>\n"
    
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    
    write_file(report_dir+"/index.html", report)
    print "The report has been generated to: "+report_dir+"/index.html"
    
    res = []
    
    if ARGS.bin:
        res.append("Avg. BC: "+bc+"%")
    
    if ARGS.src:
        res.append("Avg. SC: "+bc_src+"%")
    
    print ", ".join(res)
    
    s_exit("Ok")

try:
    scenario()
except Exception as e:
    print traceback.format_exc()
    s_exit("Error")
