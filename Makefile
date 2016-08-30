prefix ?= /usr
tool = pkg-abidiff
modules_dir = $(DESTDIR)$(prefix)/share/$(tool)
modules = $(modules_dir)/modules
tool_dir = $(DESTDIR)$(prefix)/bin

.PHONY: install uninstall
install:
	mkdir -p $(tool_dir)
	install -m 755 $(tool).py $(tool_dir)/$(tool)
	mkdir -p $(modules)
	cp -fr modules/* $(modules)/
	chmod 755 -R $(modules)
uninstall:
	rm -f $(tool_dir)/$(tool)
	rm -fr $(modules_dir)