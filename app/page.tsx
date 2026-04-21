"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import dynamic from "next/dynamic";

const PolygonMap = dynamic(() => import("./PolygonMap"), { ssr: false });
interface Viewport {
  northeast: { lat: number; lng: number };
  southwest: { lat: number; lng: number };
}
interface Verification {
  business_status: { status: string; pass: boolean; available: boolean };
  website_liveness: { reachable: boolean; name_match: boolean; pass: boolean; available: boolean };
  phone_valid: { valid: boolean; formatted: string; pass: boolean; available: boolean };
  confidence: "high" | "medium" | "low" | "unverified";
}
interface Place {
  name: string;
  category: string;
  address: string;
  phone: string;
  website: string;
  lat: number;
  lng: number;
  postcode?: string;
  sqft?: number | null;
  sqft_source?: string;
  size_tier?: string;
  enriched?: boolean;
  description?: string;
  place_id?: string;
  business_status?: string;
  viewport?: Viewport;
  verification?: Verification;
}
interface SavedSearchMeta {
  id: string;
  name: string;
  postcodes: string;
  date: string;
  count: number;
}
type Stage = "idle" | "searching" | "enriching" | "verifying" | "complete" | "error";
type SearchMode = "postcode" | "company" | "area";
const BATCH_SIZE = 10;
const DESCRIBE_BATCH_SIZE = 10;
const CONCURRENT_DESCRIBE = 2;
const CONCURRENT_ENRICH = 2;
const VERIFY_BATCH_SIZE = 10;
function formatSqft(n: number): string {
  return n.toLocaleString("en-US");
}
function sqftSourceLabel(source?: string): string | null {
  if (!source || source === "osm") return null;
  if (source === "osm_wide") return "~";
  if (source === "viewport") return "~";
  if (source === "category") return "~";
  return null;
}
function confidenceBadge(confidence?: string) {
  if (!confidence || confidence === "unverified") return <span className="text-slate-400 text-xs">--</span>;
  const styles: Record<string, string> = {
    high: "bg-emerald-100 text-emerald-700 border-emerald-300",
    medium: "bg-amber-100 text-amber-700 border-amber-300",
    low: "bg-red-100 text-red-700 border-red-300",
  };
  const icons: Record<string, string> = { high: "\u2713", medium: "\u25CB", low: "\u2717" };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-semibold rounded border ${styles[confidence] || ""}`}>
      {icons[confidence] || ""} {confidence.charAt(0).toUpperCase() + confidence.slice(1)}
    </span>
  );
}
function parsePostcodes(input: string): string[] {
  return input
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter((s) => /^\d{5}$/.test(s));
}
function parseCompanyNames(input: string): string[] {
  return input
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 2);
}
function tierBadge(tier: string) {
  const styles: Record<string, string> = {
    Large: "bg-emerald-100 text-emerald-800 border-emerald-300",
    Medium: "bg-amber-100 text-amber-800 border-amber-300",
    Small: "bg-slate-100 text-slate-600 border-slate-300",
    Unknown: "bg-gray-100 text-gray-500 border-gray-300",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 text-xs font-semibold rounded border ${styles[tier] || styles.Unknown}`}
    >
      {tier}
    </span>
  );
}
export default function Home() {
  const [input, setInput] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("postcode");
  const [polygon, setPolygon] = useState<[number, number][]>([]);
  const [stage, setStage] = useState<Stage>("idle");
  const [places, setPlaces] = useState<Place[]>([]);
  const [enrichedCount, setEnrichedCount] = useState(0);
  const [error, setError] = useState("");
  const [searchProgress, setSearchProgress] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const [savedSearches, setSavedSearches] = useState<SavedSearchMeta[]>([]);
  const [showSaves, setShowSaves] = useState(false);
  const [savingState, setSavingState] = useState<"idle" | "saving" | "saved">("idle");
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const totalLeads = places.length;
  const withFootprint = places.filter((p) => p.sqft != null).length;
  const largeFacilities = places.filter((p) => p.size_tier === "Large").length;
  const postcodeCount = new Set(places.map((p) => p.postcode).filter(Boolean)).size;
  const highConfidence = places.filter((p) => p.verification?.confidence === "high").length;
  useEffect(() => {
    fetch("/api/saves")
      .then((r) => r.json())
      .then((data) => setSavedSearches(data.saves || []))
      .catch(() => {});
  }, []);
  const handleSave = useCallback(async () => {
    if (places.length === 0) return;
    setSavingState("saving");
    try {
      const postcodesStr = [...new Set(places.map((p) => p.postcode).filter(Boolean))].join(", ");
      const saveName = postcodesStr ? `Search ${postcodesStr}` : `Company lookup: ${input.slice(0, 60)}`;
      const res = await fetch("/api/saves", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: saveName,
          postcodes: postcodesStr || input.slice(0, 100),
          places: places.map((p) => ({
            name: p.name, category: p.category, address: p.address,
            phone: p.phone, website: p.website, lat: p.lat, lng: p.lng,
            postcode: p.postcode || "", sqft: p.sqft ?? null,
            sqft_source: p.sqft_source || "", size_tier: p.size_tier || "",
            enriched: p.enriched ?? false, description: p.description || "",
            business_status: p.business_status || "",
            confidence: p.verification?.confidence || "",
          })),
        }),
      });
      if (res.ok) {
        setSavingState("saved");
        const listRes = await fetch("/api/saves");
        const listData = await listRes.json();
        setSavedSearches(listData.saves || []);
        setTimeout(() => setSavingState("idle"), 2000);
      } else {
        setSavingState("idle");
        setError("Failed to save search.");
      }
    } catch {
      setSavingState("idle");
      setError("Failed to save search.");
    }
  }, [places]);
  const handleLoadSave = useCallback(async (id: string) => {
    setLoadingId(id);
    try {
      const res = await fetch(`/api/saves?id=${id}`);
      if (!res.ok) throw new Error("Load failed");
      const data = await res.json();
      const loaded = (data.places || []) as Place[];
      setPlaces(loaded.map((p) => ({ ...p, enriched: true })));
      setInput(data.postcodes || "");
      setStage("complete");
      setEnrichedCount(loaded.length);
      setError("");
      setShowSaves(false);
    } catch {
      setError("Failed to load saved search.");
    } finally {
      setLoadingId(null);
    }
  }, []);
  const handleDeleteSave = useCallback(async (id: string) => {
    try {
      await fetch(`/api/saves?id=${id}`, { method: "DELETE" });
      setSavedSearches((prev) => prev.filter((s) => s.id !== id));
    } catch {
      setError("Failed to delete saved search.");
    }
  }, []);
  const fetchDescriptions = useCallback(async (allPlaces: Place[], abort: AbortController) => {
    const jobs: { start: number; batch: Place[] }[] = [];
    for (let i = 0; i < allPlaces.length; i += DESCRIBE_BATCH_SIZE) {
      jobs.push({ start: i, batch: allPlaces.slice(i, i + DESCRIBE_BATCH_SIZE) });
    }
    let jobIdx = 0;
    const runNext = async (): Promise<void> => {
      while (jobIdx < jobs.length) {
        if (abort.signal.aborted) break;
        const idx = jobIdx++;
        const { start, batch } = jobs[idx];
        try {
          const res = await fetch("/api/describe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              businesses: batch.map((p) => ({
                name: p.name,
                category: p.category,
                address: p.address,
                website: p.website || "",
              })),
            }),
            signal: abort.signal,
          });
          if (res.ok) {
            const data = (await res.json()) as { descriptions: Record<string, string> };
            if (data.descriptions && Object.keys(data.descriptions).length > 0) {
              setPlaces((prev) => {
                const updated = [...prev];
                for (let i = start; i < Math.min(start + DESCRIBE_BATCH_SIZE, updated.length); i++) {
                  const name = updated[i].name;
                  if (data.descriptions[name]) {
                    updated[i] = { ...updated[i], description: data.descriptions[name] };
                  }
                }
                return updated;
              });
            }
          }
        } catch {
          if (abort.signal.aborted) break;
        }
      }
    };
    const workers = Array.from({ length: Math.min(CONCURRENT_DESCRIBE, jobs.length) }, () => runNext());
    await Promise.all(workers);
  }, []);
  const fetchVerification = useCallback(async (allPlaces: Place[], abort: AbortController) => {
    for (let i = 0; i < allPlaces.length; i += VERIFY_BATCH_SIZE) {
      if (abort.signal.aborted) break;
      const batch = allPlaces.slice(i, i + VERIFY_BATCH_SIZE);
      try {
        const res = await fetch("/api/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            leads: batch.map((p) => ({
              name: p.name,
              phone: p.phone || "",
              website: p.website || "",
              business_status: p.business_status || "",
            })),
          }),
          signal: abort.signal,
        });
        if (res.ok) {
          const data = (await res.json()) as { results: { name: string; verification: Verification }[] };
          const start = i;
          setPlaces((prev) => {
            const updated = [...prev];
            data.results.forEach((r, idx) => {
              const globalIdx = start + idx;
              if (globalIdx < updated.length) {
                updated[globalIdx] = { ...updated[globalIdx], verification: r.verification };
              }
            });
            return updated;
          });
        }
      } catch {
        if (abort.signal.aborted) break;
      }
    }
  }, []);
  const runPipeline = useCallback(async () => {
    const isCompanyMode = searchMode === "company";
    const isAreaMode = searchMode === "area";
    const postcodes = isCompanyMode || isAreaMode ? [] : parsePostcodes(input);
    const companies = isCompanyMode ? parseCompanyNames(input) : [];
    const searchItems = isAreaMode
      ? ["area"]
      : isCompanyMode
        ? companies
        : postcodes;

    if (isAreaMode && polygon.length < 3) {
      setError("Please draw an area on the map (at least 3 vertices).");
      return;
    }
    if (!isAreaMode && searchItems.length === 0) {
      setError(
        isCompanyMode
          ? "Please enter one or more company names (comma or newline separated)."
          : "Please enter one or more valid 5-digit Indonesian postcodes (comma or space separated).",
      );
      return;
    }
    setError("");
    setPlaces([]);
    setEnrichedCount(0);
    setStage("searching");
    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const allPlaces: Place[] = [];
      const skipped: string[] = [];
      const debugMessages: string[] = [];
      for (let i = 0; i < searchItems.length; i++) {
        if (abort.signal.aborted) break;
        const item = searchItems[i];
        setSearchProgress(
          isAreaMode
            ? `Searching drawn area...`
            : isCompanyMode
              ? `Looking up company ${i + 1}/${searchItems.length} (${item})...`
              : `Searching postcode ${i + 1}/${searchItems.length} (${item})...`,
        );
        try {
          const body = isAreaMode
            ? { mode: "polygon", polygon }
            : isCompanyMode
              ? { mode: "company", company: item }
              : { mode: "postcode", postcode: item };
          const searchRes = await fetch("/api/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: abort.signal,
          });
          if (searchRes.ok) {
            const data = await searchRes.json();
            const foundPlaces = data.places as Place[];
            if (data.debug) console.log(`[${item}] Search debug:`, data.debug);
            if (foundPlaces.length === 0 && data.debug) {
              debugMessages.push(`${item}: ${(data.debug as string[]).join("; ")}`);
            }
            const tagged = foundPlaces.map((p) => ({ ...p, postcode: (isCompanyMode || isAreaMode) ? (p.postcode || "") : item, enriched: false }));
            allPlaces.push(...tagged);
            setPlaces([...allPlaces]);
          } else {
            skipped.push(item);
          }
        } catch (err: unknown) {
          if (abort.signal.aborted) break;
          skipped.push(item);
        }
      }
      setSearchProgress("");
      if (allPlaces.length === 0) {
        throw new Error(
          isAreaMode
            ? `No results found inside the drawn area. Try a larger polygon.${debugMessages.length > 0 ? " Debug: " + debugMessages.join(" | ") : ""}`
            : isCompanyMode
              ? `No results found. Check the company names and try again.${debugMessages.length > 0 ? " Debug: " + debugMessages.join(" | ") : ""}`
              : `No results found. Try nearby industrial areas.${debugMessages.length > 0 ? " Debug: " + debugMessages.join(" | ") : ""}`,
        );
      }
      if (skipped.length > 0) {
        setError(`Skipped (no results or error): ${skipped.join(", ")}`);
      }
      const seen = new Set<string>();
      const deduped: Place[] = [];
      for (const p of allPlaces) {
        const key = `${p.name.toLowerCase()}|${p.address.toLowerCase()}`;
        if (!seen.has(key)) {
          seen.add(key);
          deduped.push(p);
        }
      }
      setPlaces(deduped);
      setStage("enriching");
      const enrichPromise = (async () => {
        const enrichJobs: { start: number; batch: { lat: number; lng: number; name: string }[] }[] = [];
        for (let i = 0; i < deduped.length; i += BATCH_SIZE) {
          enrichJobs.push({
            start: i,
            batch: deduped.slice(i, i + BATCH_SIZE).map((p) => ({ lat: p.lat, lng: p.lng, name: p.name, viewport: p.viewport || null, business_type: p.category || "", address: p.address || "" })),
          });
        }
        let enrichCount = 0;
        let jobIdx = 0;
        const runEnrich = async (): Promise<void> => {
          while (jobIdx < enrichJobs.length) {
            if (abort.signal.aborted) break;
            const jIdx = jobIdx++;
            const { start, batch } = enrichJobs[jIdx];
            try {
              const enrichRes = await fetch("/api/enrich", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ places: batch }),
                signal: abort.signal,
              });
              if (enrichRes.ok) {
                const { results } = (await enrichRes.json()) as {
                  results: { name: string; sqft: number | null; size_tier: string; sqft_source: string }[];
                };
                setPlaces((prev) => {
                  const updated = [...prev];
                  results.forEach((r, idx) => {
                    const globalIdx = start + idx;
                    if (globalIdx < updated.length) {
                      updated[globalIdx] = { ...updated[globalIdx], sqft: r.sqft, size_tier: r.size_tier, sqft_source: r.sqft_source, enriched: true };
                    }
                  });
                  return updated;
                });
                enrichCount += results.length;
                setEnrichedCount(enrichCount);
              }
            } catch {
              if (abort.signal.aborted) break;
              setPlaces((prev) => {
                const updated = [...prev];
                for (let idx = start; idx < Math.min(start + BATCH_SIZE, updated.length); idx++) {
                  updated[idx] = { ...updated[idx], enriched: true };
                }
                return updated;
              });
              enrichCount += batch.length;
              setEnrichedCount(enrichCount);
            }
          }
        };
        const workers = Array.from({ length: Math.min(CONCURRENT_ENRICH, enrichJobs.length) }, () => runEnrich());
        await Promise.all(workers);
      })();
      const describePromise = fetchDescriptions(deduped, abort);
      await Promise.all([enrichPromise, describePromise]);
      setPlaces((prev) =>
        prev.map((p) => ({ ...p, description: p.description || "No description available", enriched: true }))
      );
      // Verification step
      setStage("verifying");
      await fetchVerification(deduped, abort);
      setStage("complete");
    } catch (err: unknown) {
      if (abort.signal.aborted) return;
      setError(err instanceof Error ? err.message : "An unexpected error occurred.");
      setStage("error");
    }
  }, [input, searchMode, polygon, fetchDescriptions, fetchVerification]);
  const handleExport = useCallback(async () => {
    const rows = places.map((p) => ({
      name: p.name, category: p.category, address: p.address,
      phone: p.phone, website: p.website, postcode: p.postcode || "",
      sqft: p.sqft ?? null, sqft_source: p.sqft_source || "",
      size_tier: p.size_tier || "Unknown", description: p.description || "",
      confidence: p.verification?.confidence || "",
    }));
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!res.ok) { setError("Failed to generate Excel file."); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `PE_Leads_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [places]);
  const handleReset = () => {
    abortRef.current?.abort();
    setStage("idle");
    setPlaces([]);
    setEnrichedCount(0);
    setError("");
    setSearchProgress("");
    setInput("");
    setPolygon([]);
  };
  const isRunning = stage === "searching" || stage === "enriching" || stage === "verifying";
  const postcodes = parsePostcodes(input);
  const companyNames = parseCompanyNames(input);
  const hasValidInput = searchMode === "company" ? companyNames.length > 0 : postcodes.length > 0;
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <header className="bg-[#0f1a2e] text-white shadow-lg">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-blue-600 flex items-center justify-center font-bold text-lg">PE</div>
              <div>
                <h1 className="text-xl font-bold tracking-tight">Indonesia Deal Sourcer</h1>
                <p className="text-sm text-slate-400">Manufacturing &amp; Industrial Target Identification</p>
              </div>
            </div>
            <button
              onClick={() => setShowSaves(!showSaves)}
              className="rounded-lg border border-slate-600 hover:border-slate-400 text-slate-300 hover:text-white font-medium px-4 py-2 text-sm transition-colors flex items-center gap-2"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
              </svg>
              Saved Searches
              {savedSearches.length > 0 && (
                <span className="bg-blue-600 text-white text-xs rounded-full px-1.5 py-0.5 min-w-[20px] text-center">{savedSearches.length}</span>
              )}
            </button>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6">
        {showSaves && (
          <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-slate-800">Saved Searches</h2>
              <button onClick={() => setShowSaves(false)} className="text-slate-400 hover:text-slate-600">
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            {savedSearches.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-6">No saved searches yet. Run a search and click &quot;Save Search&quot; to save it.</p>
            ) : (
              <div className="space-y-2">
                {savedSearches.map((s) => (
                  <div key={s.id} className="flex items-center justify-between gap-4 p-3 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">{s.name}</p>
                      <p className="text-xs text-slate-500">{s.date} &middot; {s.count} leads &middot; {s.postcodes}</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button onClick={() => handleLoadSave(s.id)} disabled={loadingId === s.id}
                        className="rounded-md bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium px-3 py-1.5 transition-colors disabled:opacity-50">
                        {loadingId === s.id ? "Loading..." : "Load"}
                      </button>
                      <button onClick={() => handleDeleteSave(s.id)}
                        className="rounded-md border border-red-200 text-red-600 hover:bg-red-50 text-xs font-medium px-3 py-1.5 transition-colors">
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
        <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <div className="flex items-center gap-1 mb-4 bg-slate-100 rounded-lg p-1 w-fit">
            <button
              onClick={() => { if (!isRunning) { setSearchMode("postcode"); setInput(""); } }}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${searchMode === "postcode" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}
            >
              By Postcode
            </button>
            <button
              onClick={() => { if (!isRunning) { setSearchMode("company"); setInput(""); } }}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${searchMode === "company" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}
            >
              By Company Name
            </button>
            <button
              onClick={() => { if (!isRunning) { setSearchMode("area"); setInput(""); } }}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${searchMode === "area" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}
            >
              Draw Area
            </button>
          </div>
          {searchMode === "area" ? (
            <>
              <PolygonMap polygon={polygon} onChange={setPolygon} />
              <div className="flex items-center gap-3 mt-4">
                <p className="flex-1 text-xs text-slate-500">
                  {polygon.length === 0
                    ? "Click on the map to start drawing the search area."
                    : `${polygon.length} vertex${polygon.length === 1 ? "" : "es"} drawn${polygon.length < 3 ? ` — need ${3 - polygon.length} more` : " — ready to search"}`}
                </p>
                {polygon.length > 0 && !isRunning && (
                  <button onClick={() => setPolygon([])}
                    className="rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 font-medium px-4 py-2 text-sm transition-colors whitespace-nowrap">
                    Clear
                  </button>
                )}
                <button onClick={runPipeline} disabled={isRunning || polygon.length < 3}
                  className="rounded-lg bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white font-semibold px-6 py-2.5 text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 whitespace-nowrap">
                  {isRunning ? (
                    <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>Processing...</>
                  ) : "Search Area"}
                </button>
                {(stage === "complete" || stage === "error" || places.length > 0) && !isRunning && (
                  <button onClick={handleReset} className="rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 font-medium px-4 py-2.5 text-sm transition-colors whitespace-nowrap">Reset</button>
                )}
              </div>
            </>
          ) : (
          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-end">
            <div className="flex-1 w-full">
              <label htmlFor="searchInput" className="block text-sm font-medium text-slate-700 mb-1">
                {searchMode === "company" ? (
                  <>Company Names <span className="text-slate-400 font-normal ml-1">(comma or newline separated)</span></>
                ) : (
                  <>Indonesia Postcodes <span className="text-slate-400 font-normal ml-1">(comma or space separated)</span></>
                )}
              </label>
              {searchMode === "company" ? (
                <textarea id="searchInput"
                  placeholder={"e.g. Astra International, Indofood, Semen Indonesia"}
                  value={input} onChange={(e) => setInput(e.target.value)} disabled={isRunning}
                  rows={3}
                  className="w-full rounded-lg border border-slate-300 px-4 py-2.5 text-base focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:bg-slate-50 resize-none"
                />
              ) : (
                <input id="searchInput" type="text" placeholder="e.g. 10110, 40111, 60111, 20112"
                  value={input} onChange={(e) => setInput(e.target.value)} disabled={isRunning}
                  className="w-full rounded-lg border border-slate-300 px-4 py-2.5 text-base focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:bg-slate-50"
                />
              )}
              {input && !isRunning && (
                <p className="mt-1 text-xs text-slate-400">
                  {searchMode === "company"
                    ? `${companyNames.length} company name${companyNames.length !== 1 ? "s" : ""} detected`
                    : `${postcodes.length} valid postcode${postcodes.length !== 1 ? "s" : ""} detected`}
                </p>
              )}
            </div>
            <button onClick={runPipeline} disabled={isRunning || !hasValidInput}
              className="rounded-lg bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white font-semibold px-6 py-2.5 text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 whitespace-nowrap">
              {isRunning ? (
                <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>Processing...</>
              ) : searchMode === "company" ? "Look Up Companies" : "Source Deals"}
            </button>
            {(stage === "complete" || stage === "error" || places.length > 0) && !isRunning && (
              <button onClick={handleReset} className="rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 font-medium px-4 py-2.5 text-sm transition-colors whitespace-nowrap">Reset</button>
            )}
          </div>
          )}
          {searchProgress && <div className="mt-3 text-sm text-blue-600 font-medium">{searchProgress}</div>}
          {error && <div className="mt-4 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}
        </section>
        {stage !== "idle" && (
          <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <div className="flex items-center gap-2 sm:gap-8 overflow-x-auto">
              <StepIndicator label="Geocoding" status={stage === "searching" ? "active" : "done"} />
              <StepConnector />
              <StepIndicator label={`Searching Places${totalLeads > 0 ? ` (${totalLeads})` : ""}`} status={stage === "searching" ? "active" : totalLeads > 0 ? "done" : "pending"} />
              <StepConnector />
              <StepIndicator label={`Enriching${enrichedCount > 0 ? ` (${enrichedCount}/${totalLeads})` : ""}`} status={stage === "enriching" ? "active" : (stage === "verifying" || stage === "complete") ? "done" : "pending"} />
              <StepConnector />
              <StepIndicator label="Verifying" status={stage === "verifying" ? "active" : stage === "complete" ? "done" : "pending"} />
              <StepConnector />
              <StepIndicator label="Complete" status={stage === "complete" ? "done" : "pending"} />
            </div>
          </section>
        )}
        {places.length > 0 && (
          <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label="Total Leads" value={totalLeads.toString()} />
            <StatCard label="Large Facilities" value={largeFacilities.toString()} accent="emerald" />
            <StatCard label="Verified (High)" value={highConfidence > 0 ? `${highConfidence} / ${totalLeads}` : `${withFootprint} / ${totalLeads}`} />
            <StatCard label="Postcodes Searched" value={postcodeCount.toString()} accent="blue" />
          </section>
        )}
        {places.length > 0 && (
          <section className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h2 className="font-semibold text-slate-800">Sourced Leads ({places.length})</h2>
              <div className="flex items-center gap-2">
                <button onClick={handleSave} disabled={savingState !== "idle"}
                  className={`rounded-lg font-semibold px-5 py-2 text-sm transition-colors flex items-center gap-2 ${savingState === "saved" ? "bg-emerald-100 text-emerald-700 border border-emerald-300" : "border border-slate-300 text-slate-700 hover:bg-slate-50"} disabled:opacity-50`}>
                  {savingState === "saving" ? (
                    <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>Saving...</>
                  ) : savingState === "saved" ? (
                    <><svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>Saved!</>
                  ) : (
                    <><svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" /></svg>Save Search</>
                  )}
                </button>
                <button onClick={handleExport}
                  className="rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-5 py-2 text-sm transition-colors flex items-center gap-2">
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                  Export to Excel
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left px-4 py-3 font-semibold text-slate-600 w-8">#</th>
                    <th className="text-left px-4 py-3 font-semibold text-slate-600">Postcode</th>
                    <th className="text-left px-4 py-3 font-semibold text-slate-600 min-w-[180px]">Company Name</th>
                    <th className="text-left px-4 py-3 font-semibold text-slate-600 min-w-[200px]">Business Description</th>
                    <th className="text-left px-4 py-3 font-semibold text-slate-600 min-w-[200px]">Address</th>
                    <th className="text-left px-4 py-3 font-semibold text-slate-600">Phone</th>
                    <th className="text-left px-4 py-3 font-semibold text-slate-600">Website</th>
                    <th className="text-right px-4 py-3 font-semibold text-slate-600">Est. Sq Ft</th>
                    <th className="text-center px-4 py-3 font-semibold text-slate-600">Size Tier</th>
                    <th className="text-center px-4 py-3 font-semibold text-slate-600">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {places.map((p, idx) => (
                    <tr key={idx} className={`border-b border-slate-100 hover:bg-blue-50/50 transition-colors ${idx % 2 === 1 ? "bg-slate-50/50" : ""}`}>
                      <td className="px-4 py-3 text-slate-400">{idx + 1}</td>
                      <td className="px-4 py-3 text-slate-500 font-mono text-xs">{p.postcode || "--"}</td>
                      <td className="px-4 py-3 font-medium text-slate-800">{p.name}</td>
                      <td className="px-4 py-3 text-slate-600 text-xs">
                        {p.description ? p.description : <span className="skeleton inline-block w-32 h-4" />}
                      </td>
                      <td className="px-4 py-3 text-slate-600 text-xs">{p.address || "--"}</td>
                      <td className="px-4 py-3 text-slate-600 whitespace-nowrap">{p.phone || "--"}</td>
                      <td className="px-4 py-3">
                        {p.website ? (
                          <a href={p.website} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline text-xs truncate block max-w-[160px]">
                            {p.website.replace(/^https?:\/\/(www\.)?/, "").slice(0, 30)}
                          </a>
                        ) : <span className="text-slate-400">--</span>}
                      </td>
                      <td className="px-4 py-3 text-right font-mono">
                        {!p.enriched ? <span className="skeleton inline-block w-16 h-4" /> : p.sqft != null ? (
                          <span>{sqftSourceLabel(p.sqft_source) && <span className="text-amber-500 mr-0.5" title={`Source: ${p.sqft_source === "category" ? "Category estimate" : p.sqft_source === "viewport" ? "Google estimate" : "OSM (wide radius)"}`}>{sqftSourceLabel(p.sqft_source)}</span>}{formatSqft(p.sqft)}</span>
                        ) : <span className="text-slate-400">N/A</span>}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {!p.enriched ? <span className="skeleton inline-block w-14 h-4" /> : tierBadge(p.size_tier || "Unknown")}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {!p.verification ? <span className="skeleton inline-block w-14 h-4" /> : confidenceBadge(p.verification.confidence)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {stage === "idle" && (
          <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-12 text-center">
            <div className="mx-auto w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center mb-4">
              <svg className="h-8 w-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-slate-700 mb-1">
              {searchMode === "area"
                ? "Draw an Area on the Map"
                : searchMode === "company" ? "Look Up Companies" : "Enter Indonesia Postcodes"}
            </h3>
            <p className="text-sm text-slate-500 max-w-md mx-auto">
              {searchMode === "area"
                ? "Click on the map above to define a custom search boundary. Results will only include businesses inside your drawn polygon."
                : searchMode === "company"
                  ? "Enter company names to look up their details, building footprint, and AI-powered descriptions."
                  : "Enter one or more postcodes to search for businesses. Results are enriched with building footprint data and AI-powered business descriptions."}
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2 text-xs text-slate-400">
              {searchMode === "company" ? (<>
                <span className="bg-slate-100 px-2 py-1 rounded">Astra International</span>
                <span className="bg-slate-100 px-2 py-1 rounded">Indofood</span>
                <span className="bg-slate-100 px-2 py-1 rounded">Semen Indonesia</span>
                <span className="bg-slate-100 px-2 py-1 rounded">Gudang Garam</span>
              </>) : (<>
                <span className="bg-slate-100 px-2 py-1 rounded">10110 &mdash; Jakarta Pusat</span>
                <span className="bg-slate-100 px-2 py-1 rounded">40111 &mdash; Bandung</span>
                <span className="bg-slate-100 px-2 py-1 rounded">60111 &mdash; Surabaya</span>
                <span className="bg-slate-100 px-2 py-1 rounded">20112 &mdash; Medan</span>
              </>)}
            </div>
          </section>
        )}
      </main>
      <footer className="mt-12 border-t border-slate-200 bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4 text-xs text-slate-400 flex justify-between">
          <span>Indonesia PE Deal Sourcer</span>
          <span>Data: Google Places API + OpenStreetMap + Claude AI</span>
        </div>
      </footer>
    </div>
  );
}
function StepIndicator({ label, status }: { label: string; status: "pending" | "active" | "done" }) {
  const dotClass = status === "done" ? "bg-emerald-500" : status === "active" ? "bg-blue-500 animate-pulse-slow" : "bg-slate-300";
  const textClass = status === "done" ? "text-emerald-700 font-medium" : status === "active" ? "text-blue-700 font-medium" : "text-slate-400";
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <div className={`h-3 w-3 rounded-full ${dotClass}`} />
      <span className={`text-sm ${textClass}`}>{label}</span>
    </div>
  );
}
function StepConnector() {
  return <div className="hidden sm:block h-px w-8 bg-slate-300 flex-shrink-0" />;
}
function StatCard({ label, value, accent }: { label: string; value: string; accent?: string }) {
  const borderColor = accent === "emerald" ? "border-l-emerald-500" : accent === "blue" ? "border-l-blue-500" : "border-l-slate-300";
  return (
    <div className={`bg-white rounded-xl shadow-sm border border-slate-200 border-l-4 ${borderColor} p-4`}>
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-800">{value}</p>
    </div>
  );
}
