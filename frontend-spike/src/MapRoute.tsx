// SPIKE P0: lazily loaded MapLibre chunk, measured apart from the initial bundle.
import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

export default function MapRoute() {
  const el = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const map = new maplibregl.Map({
      container: el.current!,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: [-79.38, 43.65],
      zoom: 10,
    })
    return () => map.remove()
  }, [])
  return <div className="app-shell"><div ref={el} className="flex-1" /></div>
}
