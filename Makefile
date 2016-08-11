prefix ?= /usr
tool = pkg-abidiff
modules_dir = $(prefix)/share/$(tool)
modules = $(modules_dir)/modules

install:
	cp -f $(tool).py $(prefix)/bin/$(tool)
	chmod 755 $(prefix)/bin/$(tool)
	mkdir -p $(modules)
	cp -fr modules/* $(modules)/
	chmod 755 -R $(modules)
uninstall:
	rm -f $(prefix)/bin/$(tool)
	rm -fr $(modules_dir)