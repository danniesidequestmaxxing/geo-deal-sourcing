"use client";
import { useEffect, useRef } from "react";
import { Loader } from "@googlemaps/js-api-loader";

interface PolygonMapProps {
  polygon: [number, number][];
  onChange: (polygon: [number, number][]) => void;
  height?: string;
}

const MALAYSIA_CENTER = { lat: 3.139, lng: 101.686 };

export default function PolygonMap({ polygon, onChange, height = "420px" }: PolygonMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<google.maps.Map | null>(null);
  const polyRef = useRef<google.maps.Polygon | null>(null);
  const markersRef = useRef<google.maps.Marker[]>([]);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    let cancelled = false;
    const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY || "";
    const loader = new Loader({ apiKey, version: "weekly" });

    (async () => {
      await loader.importLibrary("maps");
      await loader.importLibrary("marker");
      if (cancelled || !containerRef.current || mapRef.current) return;

      const map = new google.maps.Map(containerRef.current, {
        center: MALAYSIA_CENTER,
        zoom: 7,
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: false,
        clickableIcons: false,
      });
      mapRef.current = map;

      const redraw = () => {
        const pts = markersRef.current.map((m) => {
          const pos = m.getPosition()!;
          return [pos.lat(), pos.lng()] as [number, number];
        });
        const path = pts.map(([lat, lng]) => ({ lat, lng }));
        if (polyRef.current) {
          polyRef.current.setPath(path);
        } else if (pts.length >= 2) {
          polyRef.current = new google.maps.Polygon({
            paths: path,
            strokeColor: "#059669",
            strokeWeight: 2,
            fillColor: "#059669",
            fillOpacity: 0.15,
            clickable: false,
            map,
          });
        }
        onChangeRef.current(pts);
      };

      map.addListener("click", (e: google.maps.MapMouseEvent) => {
        if (!e.latLng) return;
        const marker = new google.maps.Marker({
          position: e.latLng,
          draggable: true,
          map,
        });
        marker.addListener("drag", redraw);
        marker.addListener("click", () => {
          marker.setMap(null);
          markersRef.current = markersRef.current.filter((m) => m !== marker);
          if (markersRef.current.length < 2 && polyRef.current) {
            polyRef.current.setMap(null);
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
      markersRef.current.forEach((m) => m.setMap(null));
      markersRef.current = [];
      if (polyRef.current) {
        polyRef.current.setMap(null);
        polyRef.current = null;
      }
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!mapRef.current) return;
    if (polygon.length === 0 && markersRef.current.length > 0) {
      markersRef.current.forEach((m) => m.setMap(null));
      markersRef.current = [];
      if (polyRef.current) {
        polyRef.current.setMap(null);
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
