import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AnalysisResult,
  type Detection,
  type DeploymentCorrelation,
  type KnowledgeBaseRule,
  getAnalysis,
  getKnowledgeBase,
  listAnalyses,
  uploadLog,
} from "./api";

function formatTime(iso: string | null): string {
  if (!iso) return "unknown time";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  });
}

function DeploymentList({ items }: { items: DeploymentCorrelation[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="section">
      <h4>Correlated deployments</h4>
      <ul className="bullet-list">
        {items.map((dc, i) => (
          <li key={i}>
            {[dc.service, dc.version].filter(Boolean).join(" ") || "deployment"}
            {dc.deployed_at ? ` at ${formatTime(dc.deployed_at)}` : ""}
            {dc.note ? ` — ${dc.note}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DetectionCard({ detection }: { detection: Detection }) {
  return (
    <div className="detection-card">
      <div className="detection-header">
        <span className="detection-type">{detection.type}</span>
        <span className="detection-time">{formatTime(detection.peak_time)}</span>
      </div>
      <div className="detection-meta">
        <span className="badge">{detection.error_count} errors</span>
        {detection.matched_signature ? (
          <span className="badge subtle">{detection.matched_signature}</span>
        ) : null}
        <span className="badge subtle">
          confidence {(detection.confidence * 100).toFixed(0)}%
        </span>
      </div>

      <div className="section">
        <h4>Possible cause</h4>
        <p className="cause">
          {detection.possible_cause ?? "No matching signature in knowledge base."}
        </p>
      </div>

      {detection.related_errors.length > 0 && (
        <div className="section">
          <h4>Related errors</h4>
          <ul className="bullet-list">
            {detection.related_errors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {detection.suggested_steps.length > 0 && (
        <div className="section">
          <h4>Suggested investigation</h4>
          <ol className="ordered-list">
            {detection.suggested_steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      <DeploymentList items={detection.deployment_correlations} />
    </div>
  );
}

function ResultView({ result }: { result: AnalysisResult }) {
  return (
    <div>
      <div className="summary">
        <div className="stat">
          <span className="stat-value">{result.total_entries}</span>
          <span className="stat-label">total entries</span>
        </div>
        <div className="stat">
          <span className="stat-value">{result.error_count}</span>
          <span className="stat-label">error entries</span>
        </div>
        <div className="stat">
          <span className="stat-value">{result.detections.length}</span>
          <span className="stat-label">detections</span>
        </div>
        <div className="stat filename">
          <span className="stat-value">{result.filename}</span>
          <span className="stat-label">file</span>
        </div>
      </div>

      <DeploymentList items={result.deployment_correlations} />

      {result.detections.length === 0 ? (
        <p className="empty">No spikes detected in this log.</p>
      ) : (
        result.detections.map((d, i) => <DetectionCard key={i} detection={d} />)
      )}
    </div>
  );
}

export default function App() {
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [history, setHistory] = useState<AnalysisResult[]>([]);
  const [rules, setRules] = useState<KnowledgeBaseRule[]>([]);
  const [deployFile, setDeployFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const deployInputRef = useRef<HTMLInputElement>(null);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await listAnalyses());
    } catch {
      // History is non-critical; ignore failures so upload still works.
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
    getKnowledgeBase()
      .then((kb) => setRules(kb.rules))
      .catch(() => setRules([]));
  }, [refreshHistory]);

  const handleUpload = useCallback(
    async (file: File) => {
      setLoading(true);
      setError(null);
      try {
        const res = await uploadLog(file, deployFile);
        setResult(res);
        await refreshHistory();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setLoading(false);
      }
    },
    [refreshHistory, deployFile],
  );

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) void handleUpload(file);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void handleUpload(file);
  };

  const loadHistoryItem = async (id: number | null) => {
    if (id == null) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await getAnalysis(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load analysis");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>LogSleuth</h1>
        <p className="tagline">AI Log Investigation Platform</p>
      </header>

      <main className="layout">
        <section className="main-col">
          <div
            className={`dropzone${dragging ? " dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <p>
              <strong>Drop a log file here</strong> or click to select
            </p>
            <p className="hint">Plain-text or JSON logs are supported.</p>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden-input"
              onChange={handleFileChange}
            />
          </div>

          <div className="deploy-picker">
            <button
              type="button"
              className="link-button"
              onClick={() => deployInputRef.current?.click()}
            >
              {deployFile
                ? `Deployment timeline: ${deployFile.name}`
                : "Add deployment timeline (CSV, optional)"}
            </button>
            {deployFile ? (
              <button
                type="button"
                className="link-button subtle"
                onClick={() => {
                  setDeployFile(null);
                  if (deployInputRef.current) deployInputRef.current.value = "";
                }}
              >
                clear
              </button>
            ) : null}
            <p className="hint">
              Optional: correlate error spikes with nearby deployments. Select
              the CSV before choosing a log file.
            </p>
            <input
              ref={deployInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden-input"
              onChange={(e) => setDeployFile(e.target.files?.[0] ?? null)}
            />
          </div>

          {loading && <p className="status loading">Analyzing log…</p>}
          {error && <p className="status error">Error: {error}</p>}

          {result && !loading && <ResultView result={result} />}
        </section>

        <aside className="side-col">
          <div className="panel">
            <h3>Analyses</h3>
            {history.length === 0 ? (
              <p className="hint">No analyses yet.</p>
            ) : (
              <ul className="history-list">
                {history.map((h) => (
                  <li key={h.analysis_id ?? h.filename}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => void loadHistoryItem(h.analysis_id)}
                    >
                      {h.filename}
                    </button>
                    <span className="hint">
                      {h.detections.length} detection
                      {h.detections.length === 1 ? "" : "s"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="panel">
            <h3>Knowledge base</h3>
            {rules.length === 0 ? (
              <p className="hint">No rules loaded.</p>
            ) : (
              <ul className="kb-list">
                {rules.map((r) => (
                  <li key={r.id}>
                    <span className="kb-name">
                      {r.name}
                      {r.critical ? (
                        <span className="badge subtle">critical</span>
                      ) : null}
                    </span>
                    {r.possible_cause ? (
                      <span className="hint">{r.possible_cause}</span>
                    ) : null}
                    {r.verify_note ? (
                      <span className="hint verify-note">⚠ {r.verify_note}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
