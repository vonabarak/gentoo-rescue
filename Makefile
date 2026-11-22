CORVUS_SHARE_DIR ?= /usr/share/corvus
PYTHON ?= python3
IMAGE_POLICY ?= update
GENTOO_CACHE_DIR ?= /data/corvus-cache
USEPKG ?= true
GETBINPKG ?= true
GENTOO_KEYSERVER ?= hkps://keys.gentoo.org
OUTPUT_DIR ?= build/gentoo-rescue

.DEFAULT_GOAL := all
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
export CRV ?= crv

GENTOO_BUILDER := gentoo-glibc-systemd-builder-cloudinit
OUTPUTS := disk:gentoo-rescue template:gentoo-rescue-bake template:gentoo-rescue
IMAGE_CHECK := $(CORVUS_SHARE_DIR)/yaml/check-images.sh
BUILD_ARGS := --var cache_dir="$(GENTOO_CACHE_DIR)" --var USEPKG="$(USEPKG)" \
	--var GETBINPKG="$(GETBINPKG)" --var portage_keyserver="$(GENTOO_KEYSERVER)"

define ensure-image
	+@status=0; bash "$(IMAGE_CHECK)" "disk:$(1):latest" || status=$$?; \
	case $$status in \
	  0) ;; \
	  3) $(MAKE) -C "$(CORVUS_SHARE_DIR)/yaml" image-ensure IMAGE="$(1)" \
	    PYTHON="$(PYTHON)" GENTOO_CACHE_DIR="$(GENTOO_CACHE_DIR)" \
	    USEPKG="$(USEPKG)" GETBINPKG="$(GETBINPKG)" GENTOO_KEYSERVER="$(GENTOO_KEYSERVER)" ;; \
	  *) exit $$status ;; \
	esac
endef

.PHONY: all build ensure dependencies publish check clean rebuild cache-clean
.PHONY: bundle plan export upload test test-integration
all: build

build: dependencies
	+$(MAKE) --no-print-directory publish IMAGE_POLICY="$(IMAGE_POLICY)"

dependencies:
	$(call ensure-image,$(GENTOO_BUILDER))

publish: bundle
	"$(CRV)" build rescue.yml --var image_if_exists="$(IMAGE_POLICY)" $(BUILD_ARGS) --wait

ensure:
	+@status=0; bash "$(IMAGE_CHECK)" $(OUTPUTS) || status=$$?; \
	case $$status in \
	  0) ;; \
	  3) $(MAKE) --no-print-directory build IMAGE_POLICY=skip ;; \
	  *) exit $$status ;; \
	esac

check:
	@bash "$(IMAGE_CHECK)" $(OUTPUTS)

rebuild:
	+$(MAKE) --no-print-directory build IMAGE_POLICY=overwrite

clean:
	rm -rf build

cache-clean:
	@:

bundle:
	"$(PYTHON)" -m corvus_image_builder.bundle build.py build.pyz --include-module rescue_stages.py

plan: bundle
	IMAGE_BUILD_PLAN=1 "$(PYTHON)" -I build.pyz

export:
	$(call ensure-image,$(GENTOO_BUILDER))
	"$(PYTHON)" export.py "$(OUTPUT_DIR)"

upload:
	"$(PYTHON)" upload.py "$(OUTPUT_DIR)"

test:
	CORVUS_SHARE_DIR="$(CORVUS_SHARE_DIR)" "$(PYTHON)" -m pytest -m 'not integration'

test-integration:
	"$(PYTHON)" -m pytest -m integration

.PHONY: usb-write usb-update
usb-write: export DEVICE := $(DEVICE)
usb-write:
	"$(PYTHON)" write_usb.py "$$DEVICE"

usb-update: export DEVICE := $(DEVICE)
usb-update:
	"$(PYTHON)" update_usb.py "$$DEVICE" --source "$(OUTPUT_DIR)"
