import { useState, useEffect, useCallback, type FormEvent } from 'react';
import axios from 'axios';
import {
  Search, Image as ImageIcon, Video, Link as LinkIcon,
  LoaderCircle, CircleAlert, ExternalLink, ImageOff,
  Download, Shield, ShieldCheck, Users, X, LogIn, Trash2,
  ChevronDown, Check, Zap, Globe, Play
} from 'lucide-react';
import './index.css';

interface MediaItem {
  url: string;
  title: string;
  thumbnail?: string;
  source_url?: string;
  type: string;
}

interface ScrapeResults {
  images?: MediaItem[];
  videos?: MediaItem[];
  links?: MediaItem[];
}

interface AuthSession {
  platform: string;
  display_name: string;
  username: string;
  logged_in: boolean;
}

/**
 * Main Application Component: Project Scraper
 * 
 * Orchestrates the full-stack media extraction workflow, including:
 * - URL validation and stealth-mode toggling
 * - Dynamic media extraction via FastAPI + Playwright
 * - Binary file download handling with browser fallbacks
 * - Social media session management (Instagram, TikTok, etc.)
 */
function App() {
  // --- UI & Scraper State ---
  const [urlInput, setUrlInput] = useState('');
  const [targets, setTargets] = useState<string[]>(['images']);
  const [isLoading, setIsLoading] = useState(false);
  const [results, setResults] = useState<ScrapeResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scrapedUrls, setScrapedUrls] = useState<string[]>([]);
  
  // Stealth mode allows bypassing anti-bot measures by spawning a headless browser
  const [stealthMode, setStealthMode] = useState(false);
  const [scrapeMode, setScrapeMode] = useState('');

  // --- Auth & Session State ---
  const [showAccountsModal, setShowAccountsModal] = useState(false);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loginPlatform, setLoginPlatform] = useState('instagram.com');
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginMessage, setLoginMessage] = useState<string | null>(null);

  // Tracks active downloads to show individual progress indicators
  const [downloadingUrls, setDownloadingUrls] = useState<Set<string>>(new Set());

  useEffect(() => {
    document.title = 'Project Scraper';
  }, []);

  /**
   * Fetches active login sessions from the backend.
   * Sessions are used by Stealth Mode to access gated content.
   */
  const fetchSessions = useCallback(async () => {
    try {
      const res = await axios.get('/api/auth/sessions');
      setSessions(res.data.sessions || []);
    } catch {
      // Session fetching is non-critical for core functionality; 
      // failure only impacts account-specific features.
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const toggleTarget = (target: string) => {
    setTargets(prev =>
      prev.includes(target)
        ? prev.filter(t => t !== target)
        : [...prev, target]
    );
  };

  /**
   * Generates a YouTube thumbnail URL for a given video or page link.
   * Supports standard watch links, shorts, and live streams.
   */
  const getYoutubeThumbnail = (videoUrl: string, sourceUrl?: string) => {
    const check = (u: string) => {
      if (u.includes('youtube.com') || u.includes('youtu.be')) {
        let id = '';
        if (u.includes('v=')) id = u.split('v=')[1].split('&')[0];
        else if (u.includes('youtu.be/')) id = u.split('youtu.be/')[1].split('?')[0];
        else if (u.includes('/shorts/')) id = u.split('/shorts/')[1].split('?')[0];
        else if (u.includes('/live/')) id = u.split('/live/')[1].split('?')[0];
        if (id) return `https://img.youtube.com/vi/${id}/mqdefault.jpg`;
      }
      return null;
    };

    const direct = check(videoUrl);
    if (direct) return direct;
    if (sourceUrl) return check(sourceUrl);
    return null;
  };

  /**
   * Primary extraction orchestrator.
   * Handles protocol normalization, UI state reset, and backend communication.
   */
  const handleScrape = async (e: FormEvent) => {
    e.preventDefault();
    if (!urlInput.trim() || targets.length === 0) return;

    // Normalize and split URLs from textarea
    const rawUrls = urlInput.split(/[\n,]+/).map(u => u.trim()).filter(Boolean);
    const validUrls = rawUrls.map(u => {
      if (!u.startsWith('http://') && !u.startsWith('https://')) return 'https://' + u;
      return u;
    });

    setIsLoading(true);
    setError(null);
    setResults(null);
    setScrapeMode('');

    try {
      const response = await axios.post('/api/scrape', {
        urls: validUrls,
        targets: targets,
        stealth: stealthMode,
      });

      if (response.data.success) {
        const data = response.data.data;
        
        // Noise filtering for each source
        Object.keys(data).forEach(url => {
          const group = data[url];
          if (group.items.images) {
            group.items.images = group.items.images.filter((img: MediaItem) =>
              !img.url.startsWith('data:image') || img.url.length > 200
            );
          }
        });

        setResults(data);
        setScrapedUrls(validUrls);
        setScrapeMode(response.data.mode || 'static');
      } else {
        setError(response.data.error || 'Failed to scrape the URLs.');
      }
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail || err.message || 'An error occurred during scraping.');
      } else {
        setError('An unexpected error occurred.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Triggers a proxy-based download to bypass CORS and force file saving.
   * Falls back to a new tab if the backend stream fails.
   * 
   * @param {string} fileUrl - Remote URL of the asset
   */
  const handleDownload = async (fileUrl: string) => {
    setDownloadingUrls(prev => new Set(prev).add(fileUrl));
    try {
      const response = await axios.get('/api/download', {
        params: { file_url: fileUrl },
        responseType: 'blob',
      });

      const disposition = response.headers['content-disposition'];
      let filename = 'download';
      if (disposition) {
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match) filename = match[1];
      } else {
        // Filename extraction from URL when API header is missing
        const urlPath = new URL(fileUrl).pathname;
        filename = urlPath.split('/').pop() || 'download';
      }

      const blob = new Blob([response.data]);
      const blobUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      // Direct opening as a safety fallback for blocked streams
      window.open(fileUrl, '_blank');
    } finally {
      setDownloadingUrls(prev => {
        const next = new Set(prev);
        next.delete(fileUrl);
        return next;
      });
    }
  };

  /**
   * Submits credentials to establish a server-side session.
   * Successful logins refresh the 'sessions' state to enable stealth scraping.
   */
  const handleLogin = async (e: FormEvent) => {
    e.preventDefault();
    if (!loginUsername || !loginPassword) return;
    setLoginLoading(true);
    setLoginMessage(null);
    try {
      const res = await axios.post('/api/auth/login', {
        platform: loginPlatform,
        username: loginUsername,
        password: loginPassword,
      });
      setLoginMessage(res.data.message);
      if (res.data.success) {
        setLoginUsername('');
        setLoginPassword('');
        fetchSessions();
      }
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        setLoginMessage(err.response?.data?.detail || 'Login failed.');
      } else {
        setLoginMessage('Login failed.');
      }
    } finally {
      setLoginLoading(false);
    }
  };

  const handleDeleteSession = async (platform: string) => {
    try {
      await axios.delete(`/api/auth/sessions/${platform}`);
      fetchSessions();
    } catch {
      // ignore
    }
  };

  const totalResults = results
    ? Object.values(results).reduce((acc: number, group: any) => {
        const items = group.items || {};
        return acc + (items.images?.length || 0) + (items.videos?.length || 0) + (items.links?.length || 0);
      }, 0)
    : 0;

  const getSourceHost = (url?: string) => {
    if (!url) return '';
    try { return new URL(url).hostname; } catch { return url; }
  };

  const connectedCount = sessions.filter(s => s.logged_in).length;

  return (
    <div className="app-container">
      <header className="hero">
        <div className="hero-glow" />
        <div className="hero-content">
          <h1>Project Scraper</h1>
          <p>Extract media and links from any webpage instantly.</p>
        </div>
      </header>

      <main className="main-content">
        <div className="scraper-card">
          <form onSubmit={handleScrape} className="scrape-form">
            <div className="input-group">
              <div className="input-wrapper bulk-wrapper">
                <Search className="search-icon" size={20} />
                <textarea
                  id="url-input"
                  placeholder="Enter URLs (one per line or comma-separated)"
                  value={urlInput}
                  onChange={(e) => setUrlInput(e.target.value)}
                  className="url-input bulk-input"
                  required
                  rows={2}
                />
              </div>
              <button
                id="extract-btn"
                type="submit"
                className={`submit-btn ${isLoading ? 'loading' : ''}`}
                disabled={isLoading || targets.length === 0}
              >
                {isLoading ? <LoaderCircle className="spinner" size={20} /> : 'Extract'}
              </button>
            </div>

            <div className="form-options-row">
              <div className="targets-selection">
                <p className="targets-label">Extract</p>
                <div className="target-toggles">
                  <button
                    type="button"
                    id="toggle-images"
                    className={`toggle-btn ${targets.includes('images') ? 'active' : ''}`}
                    onClick={() => toggleTarget('images')}
                  >
                    <ImageIcon size={18} /> Images
                  </button>
                  <button
                    type="button"
                    id="toggle-videos"
                    className={`toggle-btn ${targets.includes('videos') ? 'active' : ''}`}
                    onClick={() => toggleTarget('videos')}
                  >
                    <Video size={18} /> Videos
                  </button>
                  <button
                    type="button"
                    id="toggle-links"
                    className={`toggle-btn ${targets.includes('links') ? 'active' : ''}`}
                    onClick={() => toggleTarget('links')}
                  >
                    <LinkIcon size={18} /> Links
                  </button>
                </div>
              </div>

              <div className="mode-controls">
                <button
                  type="button"
                  id="stealth-toggle"
                  className={`stealth-toggle ${stealthMode ? 'active' : ''}`}
                  onClick={() => setStealthMode(!stealthMode)}
                  title={stealthMode
                    ? 'Stealth Mode ON — Uses headless browser to bypass JS protections'
                    : 'Stealth Mode OFF — Fast static fetch (works for most sites)'
                  }
                >
                  {stealthMode ? <ShieldCheck size={18} /> : <Shield size={18} />}
                  <span>Stealth</span>
                  <div className={`stealth-indicator ${stealthMode ? 'on' : ''}`} />
                </button>

                <button
                  type="button"
                  id="accounts-btn"
                  className={`accounts-btn ${connectedCount > 0 ? 'has-sessions' : ''}`}
                  onClick={() => setShowAccountsModal(true)}
                  title="Manage social media accounts"
                >
                  <Users size={18} />
                  <span>Accounts</span>
                  {connectedCount > 0 && (
                    <span className="session-badge">{connectedCount}</span>
                  )}
                </button>
              </div>
            </div>
          </form>

          {error && (
            <div className="error-message">
              <CircleAlert size={20} />
              <p>{error}</p>
            </div>
          )}
        </div>

        {scrapedUrls.length > 0 && results && (
          <>
            <div className="results-summary">
            <div className="results-summary-content">
              <span>
                Found <span className="highlight">{totalResults}</span> items from{' '}
                <span className="hostname">{scrapedUrls.length > 1 ? `${scrapedUrls.length} sources` : getSourceHost(scrapedUrls[0])}</span>
              </span>
              <span className={`mode-badge ${scrapeMode}`}>
                {scrapeMode === 'stealth' ? <><Zap size={14} /> Stealth</> : <><Globe size={14} /> Static</>}
              </span>
            </div>
          </div>
            <div className="results-container">
            {Object.entries(results).map(([url, group]: [string, any]) => {
              const items = group.items;
              const hasImages = targets.includes('images') && items.images && items.images.length > 0;
              const hasVideos = targets.includes('videos') && items.videos && items.videos.length > 0;
              const hasLinks = targets.includes('links') && items.links && items.links.length > 0;
              const hasAny = hasImages || hasVideos || hasLinks;

              return (
                <div key={url} className="source-group">
                  <div className="source-group-header">
                    <div className="source-title-info">
                      <h3>{group.title}</h3>
                      <span className="source-url-text">{url}</span>
                    </div>
                    {group.error && (
                      <span className="source-error-badge">
                        <CircleAlert size={12} /> Error
                      </span>
                    )}
                  </div>

                  <div className="source-content">
                    {hasImages && (
                      <section className="result-section">
                        <div className="section-header sub-header">
                          <h4><ImageIcon size={18} /> Images ({items.images.length})</h4>
                        </div>
                        <div className="media-grid small-grid">
                          {items.images.map((img: MediaItem, i: number) => (
                            <div key={i} className="media-item" style={{ animationDelay: `${i * 30}ms` }}>
                              <img src={img.url} alt={img.title || ''} loading="lazy" />
                              <div className="media-overlay">
                                <a href={img.url} target="_blank" rel="noopener noreferrer" className="overlay-btn open-btn"><ExternalLink size={14} /></a>
                                <button className="overlay-btn download-btn" onClick={() => handleDownload(img.url)}><Download size={14} /></button>
                              </div>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}

                    {hasVideos && (
                      <section className="result-section">
                        <div className="section-header sub-header">
                          <h4><Video size={18} /> Videos ({items.videos.length})</h4>
                        </div>
                        <div className="media-grid small-grid">
                          {items.videos.map((vid: MediaItem, i: number) => {
                            const ytThumb = getYoutubeThumbnail(vid.url, vid.source_url);
                            const isYoutubePage = (vid.url.includes('youtube.com') || vid.url.includes('youtu.be')) && !vid.url.includes('googlevideo.com');
                            return (
                              <div key={i} className="media-item video-item" style={{ animationDelay: `${i * 30}ms` }}>
                                {isYoutubePage ? (
                                  <div className="yt-preview">
                                    <img src={ytThumb || ''} alt="Preview" />
                                    <Play className="play-overlay-icon" size={24} fill="currentColor" />
                                  </div>
                                ) : (
                                  <video src={vid.url} controls preload="metadata" />
                                )}
                                <div className="media-overlay">
                                  <a href={vid.url} target="_blank" rel="noopener noreferrer" className="overlay-btn open-btn"><ExternalLink size={14} /></a>
                                  <button className="overlay-btn download-btn" onClick={() => handleDownload(vid.url)}><Download size={14} /></button>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </section>
                    )}

                    {hasLinks && (
                      <section className="result-section">
                        <div className="section-header sub-header">
                          <h4><LinkIcon size={18} /> Links ({items.links.length})</h4>
                        </div>
                        <ul className="links-list compact-links">
                          {items.links.map((link: MediaItem, i: number) => (
                            <li key={i}><a href={link.url} target="_blank" rel="noopener noreferrer"><ExternalLink size={12} /> {link.title || link.url}</a></li>
                          ))}
                        </ul>
                      </section>
                    )}

                    {!hasAny && !group.error && (
                      <p className="source-empty">No media found for this source.</p>
                    )}
                    {group.error && (
                      <div className="source-error-detail">
                        <p>{group.error}</p>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            </div>
          </>
        )}
      </main>

      <footer className="app-footer">
        <p>
          Powered by <a href="https://scrapling.readthedocs.io" target="_blank" rel="noopener noreferrer">Scrapling</a>
          {' · '} Built with FastAPI &amp; React
        </p>
      </footer>

      {/* ── Accounts Modal ──────────────────────────────────────────────── */}
      {showAccountsModal && (
        <div className="modal-backdrop" onClick={() => setShowAccountsModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2><Users size={22} /> Social Accounts</h2>
              <button className="modal-close" onClick={() => setShowAccountsModal(false)}>
                <X size={20} />
              </button>
            </div>

            <div className="modal-body">
              {/* Connected Sessions */}
              <div className="sessions-section">
                <h3>Connected Accounts</h3>
                {sessions.length > 0 ? (
                  <div className="sessions-list">
                    {sessions.map((session) => (
                      <div key={session.platform} className="session-item">
                        <div className="session-info">
                          <div className="session-status">
                            <Check size={14} />
                          </div>
                          <div>
                            <strong>{session.display_name}</strong>
                            <span className="session-username">@{session.username}</span>
                          </div>
                        </div>
                        <button
                          className="session-delete-btn"
                          onClick={() => handleDeleteSession(session.platform)}
                          title="Remove account"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="sessions-empty">No accounts connected yet.</p>
                )}
              </div>

              {/* Login Form */}
              <div className="login-section">
                <h3>Add Account</h3>
                <form onSubmit={handleLogin} className="login-form">
                  <div className="select-wrapper">
                    <select
                      value={loginPlatform}
                      onChange={e => setLoginPlatform(e.target.value)}
                      className="platform-select"
                    >
                      <option value="instagram.com">Instagram</option>
                      <option value="facebook.com">Facebook</option>
                      <option value="tiktok.com">TikTok</option>
                      <option value="x.com">X (Twitter)</option>
                    </select>
                    <ChevronDown size={16} className="select-chevron" />
                  </div>
                  <input
                    type="text"
                    placeholder="Username or email"
                    value={loginUsername}
                    onChange={e => setLoginUsername(e.target.value)}
                    className="login-input"
                    required
                  />
                  <input
                    type="password"
                    placeholder="Password"
                    value={loginPassword}
                    onChange={e => setLoginPassword(e.target.value)}
                    className="login-input"
                    required
                  />
                  <button
                    type="submit"
                    className="login-submit-btn"
                    disabled={loginLoading}
                  >
                    {loginLoading
                      ? <LoaderCircle className="spinner" size={18} />
                      : <><LogIn size={18} /> Connect</>
                    }
                  </button>
                </form>
                {loginMessage && (
                  <p className="login-message">{loginMessage}</p>
                )}
                <p className="login-note">
                  Credentials are used for one-time login. Session cookies are stored locally.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
