import 'maplibre-gl/dist/maplibre-gl.css'
import type { Map as MapLibreMap, MapLibreEvent, Marker } from 'maplibre-gl'
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { track } from '@/lib/report'
import { cn } from '@/lib/utils'

// In the time F25 gives a slow answer. A style that never answers is a failed map, and the list stays.
const LOAD_MS = 15_000
const MAX_FIT_ZOOM = 12
const FAILED_TILES = 3

export type MapPoint = { lat: number; lng: number }
export type MapPin = MapPoint & { id: number; label: string; selected: boolean; seen: boolean }
export type MapHandle = { zoomIn(): void; zoomOut(): void; fit(): void; flyTo(point: MapPoint): void }

type Props = {
  styleUrl: string
  pins: MapPin[]
  points: MapPoint[]
  me: MapPoint | null
  onReady: () => void
  onFail: (event: 'map_failed' | 'map_unsupported') => void
  onZoom: (zoom: number) => void
  onPin: (id: number) => void
  onBackground: () => void
}

const PIN = 'inline-flex h-7 cursor-pointer items-center rounded-full border px-2 text-xs font-semibold whitespace-nowrap shadow-sm'
const PIN_LOOK = {
  normal: 'border-border bg-background text-foreground',
  selected: 'border-primary bg-primary text-primary-foreground',
  seen: 'border-border bg-secondary text-muted-foreground',
}

function pinClass(pin: MapPin) {
  return cn(PIN, PIN_LOOK[pin.selected ? 'selected' : pin.seen ? 'seen' : 'normal'])
}

function hasWebGl() {
  const canvas = document.createElement('canvas')
  return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'))
}

// ADR-8: MapLibre on OpenFreeMap, one pin per area. Pins are DOM, so they stay text the theme and screen readers can read.
// Remount with a new `key` to try again.
export const MapCanvas = forwardRef<MapHandle, Props>(function MapCanvas(props, ref) {
  const { styleUrl, pins, points, me } = props
  const latest = useRef(props)
  latest.current = props
  const element = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const lib = useRef<typeof import('maplibre-gl') | null>(null)
  const markers = useRef(new Map<number, { marker: Marker; button: HTMLButtonElement }>())
  const meMarker = useRef<Marker | null>(null)
  const byPerson = useRef(false)
  const [loaded, setLoaded] = useState(false)

  const fit = () => {
    const shown = latest.current.points
    if (!map.current || !lib.current || !shown.length) return
    const bounds = new lib.current.LngLatBounds()
    for (const point of shown) bounds.extend([point.lng, point.lat])
    map.current.fitBounds(bounds, { padding: 48, maxZoom: MAX_FIT_ZOOM, duration: 0 })
  }

  useImperativeHandle(ref, () => ({
    zoomIn() {
      byPerson.current = true
      map.current?.zoomIn({ duration: 150 })
    },
    zoomOut() {
      byPerson.current = true
      map.current?.zoomOut({ duration: 150 })
    },
    fit,
    flyTo(point) {
      map.current?.flyTo({ center: [point.lng, point.lat], zoom: Math.max(map.current.getZoom(), MAX_FIT_ZOOM), duration: 400 })
    },
  }))

  useEffect(() => {
    let over = false
    let instance: MapLibreMap | null = null
    const fail = (event: 'map_failed' | 'map_unsupported') => {
      if (over) return
      over = true
      latest.current.onFail(event)
    }
    const timer = window.setTimeout(() => fail('map_failed'), LOAD_MS)
    if (!hasWebGl()) {
      fail('map_unsupported')
      return () => window.clearTimeout(timer)
    }
    void import('maplibre-gl')
      .then((module) => {
        if (over || !element.current) return
        // The package is a UMD file: built, its exports can sit on `default`.
        const maplibre = (module as unknown as { default?: typeof module }).default ?? module
        lib.current = maplibre
        instance = new maplibre.Map({ container: element.current, style: styleUrl, center: [0, 20], zoom: 1, minZoom: 2, dragRotate: false, pitchWithRotate: false, touchPitch: false, attributionControl: { compact: true } })
        instance.touchZoomRotate.disableRotation()
        map.current = instance
        // The pin check (e2e/map-pins.spec.ts) measures pins against MapLibre's own projection. Not in the Production build.
        if (__TG_MOCK__ && new URLSearchParams(location.search).has('tgmock')) Object.assign(window, { __chamedoonMap: instance })
        let styled = false
        let tileOk = false
        let tileErrors = 0
        instance.once('style.load', () => void (styled = true))
        instance.on('data', (event) => {
          if ('tile' in event && event.tile) tileOk = true
        })
        // A style that fails is no map. After it, a tile can miss and the map still works, unless none ever came.
        instance.on('error', (event) => {
          if (!styled) return fail('map_failed')
          if ('tile' in event || 'sourceId' in event) tileErrors += 1
          if (tileErrors >= FAILED_TILES && !tileOk) fail('map_failed')
        })
        instance.once('load', () => {
          window.clearTimeout(timer)
          if (over) return
          fit()
          setLoaded(true)
          latest.current.onReady()
          latest.current.onZoom(instance!.getZoom())
        })
        instance.on('zoomend', () => latest.current.onZoom(instance!.getZoom()))
        instance.on('moveend', (event: MapLibreEvent) => {
          if (event.originalEvent || byPerson.current) {
            track('map_moved', { zoom: Math.round(instance!.getZoom()), by: event.originalEvent ? 'gesture' : 'button' })
          }
          byPerson.current = false
        })
        instance.on('click', () => latest.current.onBackground())
      })
      .catch(() => fail('map_failed'))
    return () => {
      over = true
      window.clearTimeout(timer)
      instance?.remove()
      map.current = null
      markers.current.clear()
      meMarker.current = null
    }
  }, [styleUrl])

  // The sheet changes the map's height.
  useEffect(() => {
    if (!element.current) return
    const observer = new ResizeObserver(() => map.current?.resize())
    observer.observe(element.current)
    return () => observer.disconnect()
  }, [])

  const firstFit = useRef(false)
  useEffect(() => {
    if (!loaded || firstFit.current || !points.length) return
    firstFit.current = true
    fit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, points])

  useEffect(() => {
    if (!loaded || !map.current || !lib.current) return
    const held = markers.current
    for (const [id, { marker }] of held) {
      if (!pins.some((pin) => pin.id === id)) {
        marker.remove()
        held.delete(id)
      }
    }
    for (const pin of pins) {
      let row = held.get(pin.id)
      if (!row) {
        const button = document.createElement('button')
        button.type = 'button'
        button.dir = 'rtl'
        button.dataset.testid = 'map-pin'
        button.dataset.area = String(pin.id)
        button.addEventListener('click', (event) => {
          event.stopPropagation()
          latest.current.onPin(pin.id)
        })
        // MapLibre owns the marker element's classes (`maplibregl-marker` is what makes it `position: absolute`).
        // The pin's look changes with its state, so it lives on a child; restyling the element itself put pins in the page flow, off their spot.
        const holder = document.createElement('div')
        holder.append(button)
        row = { button, marker: new lib.current.Marker({ element: holder }).setLngLat([pin.lng, pin.lat]).addTo(map.current) }
        held.set(pin.id, row)
      }
      row.button.className = pinClass(pin)
      row.button.textContent = pin.label
      row.button.setAttribute('aria-pressed', String(pin.selected))
      row.button.dataset.seen = String(pin.seen)
      row.marker.getElement().style.zIndex = pin.selected ? '1' : ''
    }
  }, [pins, loaded])

  useEffect(() => {
    if (!loaded || !map.current || !lib.current) return
    if (!me) {
      meMarker.current?.remove()
      meMarker.current = null
      return
    }
    if (!meMarker.current) {
      const dot = document.createElement('span')
      dot.dataset.testid = 'map-me'
      dot.setAttribute('role', 'img')
      dot.setAttribute('aria-label', 'مکان شما')
      dot.className = 'block size-4 rounded-full bg-primary shadow-[0_0_0_3px_var(--background)]'
      meMarker.current = new lib.current.Marker({ element: dot }).setLngLat([me.lng, me.lat]).addTo(map.current)
    }
    meMarker.current.setLngLat([me.lng, me.lat])
  }, [me, loaded])

  // The canvas is LTR whatever the page is: MapLibre's panning and its attribution assume it.
  // MapLibre's stylesheet sets its container `position: relative` and beats Tailwind's layer, so the wrapper is what fills the area.
  return (
    <div className="absolute inset-0">
      <div ref={element} dir="ltr" className="h-full w-full" data-testid="map-canvas" />
    </div>
  )
})
