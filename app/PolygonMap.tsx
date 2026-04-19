"use client";
import { useEffect, useRef } from "react";
import type { Map as LeafletMap, Polygon as LeafletPolygon, Marker as LeafletMarker } from "leaflet";

interface PolygonMapProps {
  polygon: [number, number][];
  onChange: (polygon: [number, number][]) => void;
  height?: string;
}

const MALAYSIA_CENTER: [number, number] = [3.139, 101.686]; // Kuala Lumpur

export default function PolygonMap({ polygon, onChange, height = "420px" }: PolygonMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const polyRef = useRef<LeafletPolygon | null>(null);
  const markersRef = useRef<LeafletMarker[]>([]);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !containerRef.current || mapRef.current) return;

      const map = L.map(containerRef.current).setView(MALAYSIA_CENTER, 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(map);
      mapRef.current = map;

      const redraw = () => {
        const pts = markersRef.current.map((m) => {
          const ll = m.getLatLng();
          return [ll.lat, ll.lng] as [number, number];
        });
        if (polyRef.current) {
          polyRef.current.setLatLngs(pts);
        } else if (pts.length >= 2) {
          polyRef.current = L.polygon(pts, { color: "#059669", weight: 2, fillOpacity: 0.15 }).addTo(map);
        }
        onChangeRef.current(pts);
      };

      map.on("click", (e: { latlng: { lat: number; lng: number } }) => {
        const marker = L.marker(e.latlng, { draggable: true }).addTo(map);
        marker.on("drag", redraw);
        marker.on("click", () => {
          map.removeLayer(marker);
          markersRef.current = markersRef.current.filter((m) => m !== marker);
          if (markersRef.current.length < 2 && polyRef.current) {
            map.removeLayer(polyRef.current);
            polyRef.current = null;
          }
          redraw();
        });
        markersRef.current.push(marker);
        redraw();
      });
    })();
    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        polyRef.current = null;
        markersRef.current = [];
      }
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (polygon.length === 0 && markersRef.current.length > 0) {
      markersRef.current.forEach((m) => map.removeLayer(m));
      markersRef.current = [];
      if (polyRef.current) {
        map.removeLayer(polyRef.current);
        polyRef.current = null;
      }
    }
  }, [polygon]);

  return (
    <div>
      <div ref={containerRef} style={{ height, width: "100%" }} className="rounded-lg border border-slate-200 overflow-hidden" />
      <p className="text-xs text-slate-500 mt-2">
        Click on the map to add vertices. Drag vertices to adjust. Click a vertex to remove it. Minimum 3 vertices required.
      </p>
    </div>
  );
}
