/* Full screen photo gallery for a listing page: swipe, previous/next, counter, pinch and
 * double-tap zoom, Esc and the Telegram back button close it. No dependencies.
 *
 * Markup (homes_detail.html): a strip of [data-gallery-open="i"] buttons inside
 * [data-gallery-strip], and one hidden [data-gallery] dialog with a [data-gallery-track]
 * of [data-gallery-slide] figures whose images carry data-src (loaded when needed).
 * In RTL the next photo sits to the left: the track lays out right to left, «next» is the
 * button on the left, and swiping the finger to the right moves forward.
 */
(function () {
  "use strict";

  var FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  var MAX_SCALE = 4;
  var DOUBLE_TAP_SCALE = 2.5;
  var DOUBLE_TAP_MS = 300;

  function digits(value, locale) {
    var text = String(value);
    if (locale !== "fa") return text;
    return text.replace(/[0-9]/g, function (d) {
      return FA_DIGITS.charAt(Number(d));
    });
  }

  function fill(template, i, n, locale) {
    return String(template || "{i}/{n}")
      .replace("{i}", digits(i, locale))
      .replace("{n}", digits(n, locale));
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function webApp() {
    return window.Telegram && window.Telegram.WebApp;
  }

  function create(root, strip) {
    var doc = root.ownerDocument;
    var html = doc.documentElement;
    var locale = root.getAttribute("data-locale") || html.lang || "en";
    var counterFormat = root.getAttribute("data-counter-format") || "{i}/{n}";
    var viewport = root.querySelector("[data-gallery-viewport]");
    var track = root.querySelector("[data-gallery-track]");
    var slides = Array.prototype.slice.call(root.querySelectorAll("[data-gallery-slide]"));
    var counter = root.querySelector("[data-gallery-counter]");
    var closeBtn = root.querySelector("[data-gallery-close]");
    var prevBtn = root.querySelector("[data-gallery-prev]");
    var nextBtn = root.querySelector("[data-gallery-next]");
    var count = slides.length;
    var index = 0;
    var isOpen = false;
    var opener = null;
    var unregisterBack = null;
    var tgBackHandler = null;

    // Gesture state.
    var pointers = {};
    var drag = null;
    var pinch = null;
    var lastTap = null;
    var lastPointerType = "mouse";
    var zoom = { scale: 1, x: 0, y: 0 };

    function rtl() {
      var dir = (root.closest && root.closest("[dir]")) ? root.closest("[dir]").getAttribute("dir") : html.getAttribute("dir");
      return String(dir || "").toLowerCase() === "rtl";
    }

    function width() {
      return (viewport && viewport.clientWidth) || root.clientWidth || 1;
    }

    function image(i) {
      var slide = slides[i];
      return slide ? slide.querySelector("img") : null;
    }

    function load(i) {
      var img = image(i);
      if (!img || img.getAttribute("src")) return;
      var src = img.getAttribute("data-src");
      if (src) img.setAttribute("src", src);
    }

    function placeTrack(offsetPx, animate) {
      if (!track) return;
      // RTL: slides run right to left, so moving forward shifts the track to the right.
      var sign = rtl() ? 1 : -1;
      track.classList.toggle("is-animating", Boolean(animate));
      track.style.transform = "translate3d(calc(" + sign * index * 100 + "% + " + (offsetPx || 0) + "px), 0, 0)";
    }

    function applyZoom(animate) {
      var img = image(index);
      if (!img) return;
      img.classList.toggle("is-animating", Boolean(animate));
      img.style.transform =
        zoom.scale === 1 ? "" : "translate3d(" + zoom.x + "px, " + zoom.y + "px, 0) scale(" + zoom.scale + ")";
      root.classList.toggle("is-zoomed", zoom.scale > 1);
    }

    function resetZoom(animate) {
      zoom = { scale: 1, x: 0, y: 0 };
      applyZoom(animate);
      slides.forEach(function (_, i) {
        if (i === index) return;
        var img = image(i);
        if (img) img.style.transform = "";
      });
    }

    function clampPan() {
      var img = image(index);
      var w = img ? img.clientWidth : width();
      var h = img ? img.clientHeight : root.clientHeight;
      var maxX = ((zoom.scale - 1) * w) / 2;
      var maxY = ((zoom.scale - 1) * h) / 2;
      zoom.x = clamp(zoom.x, -maxX, maxX);
      zoom.y = clamp(zoom.y, -maxY, maxY);
    }

    // Zoom to `scale` keeping the point (cx, cy), relative to the image centre, under the finger.
    function zoomAt(scale, cx, cy) {
      var next = clamp(scale, 1, MAX_SCALE);
      var qx = (cx - zoom.x) / zoom.scale;
      var qy = (cy - zoom.y) / zoom.scale;
      zoom.scale = next;
      zoom.x = cx - next * qx;
      zoom.y = cy - next * qy;
      if (next === 1) {
        zoom.x = 0;
        zoom.y = 0;
      }
      clampPan();
    }

    function centreOffset(clientX, clientY) {
      var img = image(index);
      var box = (img || root).getBoundingClientRect();
      // getBoundingClientRect includes the current transform; use the untransformed centre.
      var cx = box.left + box.width / 2 - zoom.x;
      var cy = box.top + box.height / 2 - zoom.y;
      return { x: clientX - cx, y: clientY - cy };
    }

    function render(animate) {
      placeTrack(0, animate);
      if (counter) counter.textContent = fill(counterFormat, index + 1, count, locale);
      slides.forEach(function (slide, i) {
        slide.setAttribute("aria-hidden", i === index ? "false" : "true");
      });
      if (prevBtn) prevBtn.disabled = index <= 0;
      if (nextBtn) nextBtn.disabled = index >= count - 1;
      var active = doc.activeElement;
      if (active && active.disabled && root.contains(active)) {
        var other = active === prevBtn ? nextBtn : prevBtn;
        (other && !other.disabled ? other : closeBtn).focus();
      }
      // Preload the neighbours so the next swipe shows a photo, not a blank.
      load(index);
      load(index - 1);
      load(index + 1);
    }

    function go(target, animate) {
      var next = clamp(target, 0, count - 1);
      if (next !== index) resetZoom(false);
      index = next;
      render(animate !== false);
      return index;
    }

    function bindBack() {
      if (typeof window.chamedoonRegisterBack === "function") {
        unregisterBack = window.chamedoonRegisterBack({
          active: function () {
            return isOpen;
          },
          back: function () {
            close();
            return true;
          },
        });
        return;
      }
      var tg = webApp();
      if (!tg || !tg.BackButton) return;
      tgBackHandler = function () {
        close();
      };
      try {
        tg.BackButton.onClick(tgBackHandler);
        tg.BackButton.show();
      } catch (e) {
        tgBackHandler = null;
      }
    }

    function unbindBack() {
      if (unregisterBack) {
        var done = unregisterBack;
        unregisterBack = null;
        done();
      }
      if (tgBackHandler) {
        var tg = webApp();
        try {
          tg.BackButton.offClick(tgBackHandler);
          tg.BackButton.hide();
        } catch (e) {}
        tgBackHandler = null;
      }
    }

    function open(target, from) {
      if (!count) return;
      opener = from || doc.activeElement;
      index = clamp(Number(target) || 0, 0, count - 1);
      resetZoom(false);
      root.hidden = false;
      html.classList.add("gallery-open");
      isOpen = true;
      render(false);
      bindBack();
      // Focus goes into the dialog for screen readers and keyboards; no ring after a tap.
      if (closeBtn) closeBtn.focus({ focusVisible: false });
    }

    function close() {
      if (!isOpen) return;
      isOpen = false;
      pointers = {};
      drag = null;
      pinch = null;
      resetZoom(false);
      root.hidden = true;
      html.classList.remove("gallery-open");
      unbindBack();
      // Back on the page, show the photo the person stopped at.
      var item = strip && strip.querySelector('[data-gallery-open="' + index + '"]');
      if (item) {
        try {
          item.scrollIntoView({ block: "nearest", inline: "center" });
        } catch (e) {}
      }
      var focusTarget = item || opener;
      if (focusTarget && typeof focusTarget.focus === "function") {
        try {
          focusTarget.focus({ preventScroll: true });
        } catch (e) {
          focusTarget.focus();
        }
      }
      if (strip && strip.__galleryBadge) strip.__galleryBadge();
    }

    // In RTL the next photo is to the left: ArrowLeft goes forward.
    function forwardKey(key) {
      return rtl() ? key === "ArrowLeft" : key === "ArrowRight";
    }

    function focusables() {
      return Array.prototype.slice.call(root.querySelectorAll("button")).filter(function (el) {
        return !el.disabled && !el.hidden && el.getClientRects().length > 0;
      });
    }

    function onKey(event) {
      if (!isOpen) return;
      var key = event.key;
      if (key === "Escape" || key === "Esc") {
        event.preventDefault();
        close();
      } else if (key === "ArrowLeft" || key === "ArrowRight") {
        event.preventDefault();
        go(index + (forwardKey(key) ? 1 : -1));
      } else if (key === "Home") {
        event.preventDefault();
        go(0);
      } else if (key === "End") {
        event.preventDefault();
        go(count - 1);
      } else if (key === "Tab") {
        var items = focusables();
        if (!items.length) {
          event.preventDefault();
          return;
        }
        var first = items[0];
        var last = items[items.length - 1];
        var active = doc.activeElement;
        if (event.shiftKey && (active === first || !root.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || !root.contains(active))) {
          event.preventDefault();
          first.focus();
        }
      }
    }

    // Keep focus inside the dialog if something outside grabs it.
    function onFocusIn(event) {
      if (isOpen && !root.contains(event.target) && closeBtn) closeBtn.focus();
    }

    function pointerList() {
      return Object.keys(pointers).map(function (id) {
        return pointers[id];
      });
    }

    function distance(a, b) {
      return Math.sqrt(Math.pow(a.x - b.x, 2) + Math.pow(a.y - b.y, 2));
    }

    function onPointerDown(event) {
      if (!isOpen || (event.button !== undefined && event.button > 0)) return;
      if (event.target && event.target.closest && event.target.closest("button")) return;
      lastPointerType = event.pointerType || "mouse";
      pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
      try {
        viewport.setPointerCapture(event.pointerId);
      } catch (e) {}
      var list = pointerList();
      if (list.length === 2) {
        drag = null;
        placeTrack(0, true);
        var mid = { x: (list[0].x + list[1].x) / 2, y: (list[0].y + list[1].y) / 2 };
        pinch = { dist: distance(list[0], list[1]) || 1, scale: zoom.scale, centre: centreOffset(mid.x, mid.y) };
      } else if (list.length === 1) {
        drag = {
          x: event.clientX,
          y: event.clientY,
          t: Date.now(),
          panX: zoom.x,
          panY: zoom.y,
          moved: false,
          axis: "",
        };
      }
    }

    function onPointerMove(event) {
      if (!pointers[event.pointerId]) return;
      pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
      if (pinch) {
        var list = pointerList();
        if (list.length < 2) return;
        var ratio = distance(list[0], list[1]) / pinch.dist;
        zoom.scale = pinch.scale;
        zoomAt(pinch.scale * ratio, pinch.centre.x, pinch.centre.y);
        applyZoom(false);
        return;
      }
      if (!drag) return;
      var dx = event.clientX - drag.x;
      var dy = event.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 8) {
        drag.moved = true;
        drag.axis = Math.abs(dx) >= Math.abs(dy) ? "x" : "y";
      }
      if (!drag.moved) return;
      if (zoom.scale > 1) {
        zoom.x = drag.panX + dx;
        zoom.y = drag.panY + dy;
        clampPan();
        applyZoom(false);
        return;
      }
      if (drag.axis === "x") {
        var forward = rtl() ? dx > 0 : dx < 0;
        var atEnd = forward ? index >= count - 1 : index <= 0;
        placeTrack(atEnd ? dx / 3 : dx, false);
      } else {
        root.style.setProperty("--gallery-drag-y", dy + "px");
        root.classList.add("is-dragging-y");
      }
    }

    function finishDrag(event) {
      var dx = event.clientX - drag.x;
      var dy = event.clientY - drag.y;
      var dt = Date.now() - drag.t;
      var state = drag;
      drag = null;
      root.classList.remove("is-dragging-y");
      root.style.removeProperty("--gallery-drag-y");
      if (!state.moved) {
        // Mouse double clicks come through dblclick; double taps are counted here.
        if (event.pointerType !== "mouse") onTap(event.clientX, event.clientY);
        return;
      }
      if (zoom.scale > 1) return;
      if (state.axis === "y") {
        if (Math.abs(dy) > 120) close();
        return;
      }
      var threshold = Math.min(80, width() * 0.18);
      var flick = Math.abs(dx) > 24 && dt < 260;
      if (Math.abs(dx) > threshold || flick) {
        var forward = rtl() ? dx > 0 : dx < 0;
        go(index + (forward ? 1 : -1));
      } else {
        placeTrack(0, true);
      }
    }

    function onTap(clientX, clientY) {
      var now = Date.now();
      if (lastTap && now - lastTap.t < DOUBLE_TAP_MS && Math.abs(clientX - lastTap.x) < 30 && Math.abs(clientY - lastTap.y) < 30) {
        lastTap = null;
        toggleZoom(clientX, clientY);
        return;
      }
      lastTap = { t: now, x: clientX, y: clientY };
    }

    function toggleZoom(clientX, clientY) {
      if (zoom.scale > 1) {
        resetZoom(true);
      } else {
        var c = centreOffset(clientX, clientY);
        zoomAt(DOUBLE_TAP_SCALE, c.x, c.y);
        applyZoom(true);
      }
    }

    function onPointerUp(event) {
      if (!pointers[event.pointerId]) return;
      delete pointers[event.pointerId];
      if (pinch) {
        if (pointerList().length < 2) {
          pinch = null;
          if (zoom.scale < 1.05) resetZoom(true);
          // The finger left on the glass keeps panning from where it is.
          var rest = pointerList()[0];
          drag = rest ? { x: rest.x, y: rest.y, t: Date.now(), panX: zoom.x, panY: zoom.y, moved: true, axis: "x" } : null;
        }
        return;
      }
      if (drag) finishDrag(event);
    }

    function onPointerCancel(event) {
      delete pointers[event.pointerId];
      pinch = null;
      drag = null;
      root.classList.remove("is-dragging-y");
      placeTrack(0, true);
    }

    if (closeBtn) closeBtn.addEventListener("click", close);
    if (prevBtn)
      prevBtn.addEventListener("click", function () {
        go(index - 1);
      });
    if (nextBtn)
      nextBtn.addEventListener("click", function () {
        go(index + 1);
      });
    root.addEventListener("keydown", onKey);
    doc.addEventListener("focusin", onFocusIn);
    if (viewport) {
      viewport.addEventListener("pointerdown", onPointerDown);
      viewport.addEventListener("pointermove", onPointerMove);
      viewport.addEventListener("pointerup", onPointerUp);
      viewport.addEventListener("pointercancel", onPointerCancel);
      viewport.addEventListener("dblclick", function (event) {
        // Mouse double click; touch double taps are handled from pointer events.
        if (lastPointerType !== "mouse") return;
        lastTap = null;
        toggleZoom(event.clientX, event.clientY);
      });
    }
    window.addEventListener("resize", function () {
      if (!isOpen) return;
      resetZoom(false);
      render(false);
    });

    if (strip) {
      strip.addEventListener("click", function (event) {
        var item = event.target && event.target.closest && event.target.closest("[data-gallery-open]");
        if (!item || !strip.contains(item)) return;
        event.preventDefault();
        open(Number(item.getAttribute("data-gallery-open")), item);
      });
      bindBadge(strip);
    }

    return {
      open: open,
      close: close,
      next: function () {
        return go(index + 1);
      },
      prev: function () {
        return go(index - 1);
      },
      go: go,
      index: function () {
        return index;
      },
      isOpen: function () {
        return isOpen;
      },
      zoom: function () {
        return zoom.scale;
      },
      toggleZoom: toggleZoom,
    };
  }

  // «۱ از ۶» on the inline strip, following its horizontal scroll.
  function bindBadge(strip) {
    var badge = strip.querySelector("[data-gallery-badge]");
    var scroller = strip.querySelector(".house-gallery");
    if (!badge || !scroller) return;
    var items = Array.prototype.slice.call(scroller.querySelectorAll("[data-gallery-open]"));
    var locale = strip.getAttribute("data-locale") || "en";
    var format = strip.getAttribute("data-badge-format") || "{i}/{n}";
    var pending = false;
    function update() {
      pending = false;
      var box = scroller.getBoundingClientRect();
      var mid = box.left + box.width / 2;
      var best = 0;
      var bestGap = Infinity;
      items.forEach(function (item, i) {
        var r = item.getBoundingClientRect();
        var gap = Math.abs(r.left + r.width / 2 - mid);
        if (gap < bestGap) {
          bestGap = gap;
          best = i;
        }
      });
      badge.textContent = fill(format, best + 1, items.length, locale);
    }
    scroller.addEventListener(
      "scroll",
      function () {
        if (pending) return;
        pending = true;
        (window.requestAnimationFrame || window.setTimeout)(update);
      },
      { passive: true }
    );
    strip.__galleryBadge = update;
  }

  function boot() {
    var root = document.querySelector("[data-gallery]");
    if (!root || root.__gallery) return;
    var strip = document.querySelector("[data-gallery-strip]");
    root.__gallery = create(root, strip);
    window.chamedoonGallery = root.__gallery;
  }

  window.ChamedoonGallery = { create: create, digits: digits, fill: fill, boot: boot };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
