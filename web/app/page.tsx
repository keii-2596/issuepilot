'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Archive,
  Bot,
  CheckCircle2,
  CircleDot,
  Clock3,
  ExternalLink,
  GitFork,
  GitCommitHorizontal,
  KeyRound,
  LayoutDashboard,
  Loader2,
  MessageSquare,
  Play,
  Radar,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Star,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';

type Manifest = {
  status: string;
  issue: string;
  workspace: string | null;
  pull_request: string | null;
  updated_at: string | null;
};

type DashboardStatus = {
  github_authenticated: boolean;
  github_mode: string;
  github_user: string | null;
  github_error: string | null;
  github_cli_available: boolean;
  codex_found: boolean;
  codex_authenticated: boolean;
  publish_unlocked: boolean;
  read_only_ready: boolean;
  publish_ready: boolean;
  workspace: string;
  filters: {
    interests?: {
      domains: string[];
      keywords: string[];
      excluded_keywords: string[];
    };
    min_stars: number;
    max_stars: number;
    languages: string[];
    repo_limit: number;
    issues_per_repo: number;
    max_repo_idle_days: number;
    max_issue_idle_days: number;
    max_issue_age_days: number;
    issue_labels: string[];
    active_work_days: number;
    discovery_scan_pages: number;
    discovery_sort: DiscoverySortMode;
  };
  discovery_next_page: number;
  domain_presets?: { id: string; label: string }[];
  candidate_archive: CandidateArchive;
  prepared: Manifest[];
};

type ScanSummary = {
  pages: number[];
  repositories_scanned: number;
  issues_collected: number;
  issues_filtered_by_age: number;
  issues_verified: number;
  target_count: number;
  target_reached: boolean;
  search_exhausted: boolean;
  scan_limit_reached: boolean;
  cancelled: boolean;
  search_page_limit: number;
  star_windows_scanned: number;
  next_page: number;
};

type Candidate = {
  score: number;
  stars: number;
  language: string | null;
  repository: string;
  issue: number;
  title: string;
  url: string;
  labels: string[];
  comments: number;
  summary: string;
  fit: 'recommended' | 'review' | 'caution';
  fit_reasons: string[];
  risk_reasons: string[];
  longevity_reason: string;
  age_days: number | null;
  updated_days: number | null;
  verification: string[];
  warnings: string[];
};

type CandidateArchive = {
  total_candidates: number;
  run_count: number;
  latest_at: string | null;
  latest_candidates: Candidate[];
  latest_filters: Partial<DiscoveryFilters> | null;
};

type Outcome = {
  status: string;
  issue: string;
  message: string;
  workspace: string | null;
  pull_request: string | null;
};

type BusyState = {
  kind: 'discover' | 'run' | 'publish' | 'archive' | 'connect';
  issue?: string;
} | null;
type Notice = { tone: 'success' | 'error' | 'info'; text: string } | null;
type RunFeedback = Exclude<Notice, null>;
type DiscoverySortMode =
  | 'recommended'
  | 'stars_desc'
  | 'stars_asc'
  | 'newest_issues'
  | 'recently_updated';
type DiscoveryFilters = {
  domains: string[];
  keywords: string;
  excluded_keywords: string;
  min_stars: number;
  max_stars: number;
  languages: string;
  max_repo_idle_days: number;
  max_issue_idle_days: number;
  max_issue_age_days: number;
  issue_labels: string;
  active_work_days: number;
  repo_limit: number;
  issues_per_repo: number;
  limit: number;
  sort_mode: DiscoverySortMode;
};

const defaultFilters: DiscoveryFilters = {
  domains: [],
  keywords: '',
  excluded_keywords: '',
  min_stars: 5000,
  max_stars: 1000000,
  languages: 'Python, TypeScript, JavaScript, Go, Rust',
  max_repo_idle_days: 180,
  max_issue_idle_days: 365,
  max_issue_age_days: 180,
  issue_labels: 'good first issue, help wanted',
  active_work_days: 45,
  repo_limit: 20,
  issues_per_repo: 5,
  limit: 12,
  sort_mode: 'recommended',
};

const filterStorageKey = 'issuepilot.discovery-filters.v1';
const numericFilterKeys: (keyof DiscoveryFilters)[] = [
  'min_stars',
  'max_stars',
  'max_repo_idle_days',
  'max_issue_idle_days',
  'max_issue_age_days',
  'active_work_days',
  'repo_limit',
  'issues_per_repo',
  'limit',
];

const sortModeLabels: Record<DiscoverySortMode, string> = {
  recommended: '综合可处理性',
  stars_desc: 'Stars 从高到低',
  stars_asc: 'Stars 从低到高',
  newest_issues: 'Issue 最新创建',
  recently_updated: 'Issue 最近更新',
};

const loadSavedFilters = (fallback: DiscoveryFilters): DiscoveryFilters => {
  try {
    const raw = window.localStorage.getItem(filterStorageKey);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<DiscoveryFilters>;
    const restored = { ...fallback };
    for (const key of numericFilterKeys) {
      const value = saved[key];
      if (typeof value === 'number' && Number.isFinite(value))
        Object.assign(restored, { [key]: value });
    }
    if (typeof saved.languages === 'string')
      restored.languages = saved.languages;
    // Domain preferences come from the local server so CLI and daily runs agree.
    if (typeof saved.issue_labels === 'string')
      restored.issue_labels = saved.issue_labels;
    if (
      typeof saved.sort_mode === 'string' &&
      saved.sort_mode in sortModeLabels
    )
      restored.sort_mode = saved.sort_mode as DiscoverySortMode;
    return restored;
  } catch {
    return fallback;
  }
};

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const headers = new Headers(options?.headers);
  if (!headers.has('Content-Type'))
    headers.set('Content-Type', 'application/json');
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const data = (await response.json().catch(() => ({}))) as {
    error?: string;
  } & T;
  if (!response.ok)
    throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
};

const formatStars = (value: number) =>
  value >= 1000
    ? `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)}k`
    : String(value);

const issueName = (url: string) => {
  const match = url.match(/github\.com\/([^/]+\/[^/]+)\/issues\/(\d+)/);
  return match ? `${match[1]} #${match[2]}` : url;
};

export default function Home() {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState<BusyState>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<Manifest | null>(null);
  const [publishText, setPublishText] = useState('');
  const [githubDeviceCode, setGitHubDeviceCode] = useState<string | null>(null);
  const [filters, setFilters] = useState<DiscoveryFilters>(defaultFilters);
  const [filtersInitialized, setFiltersInitialized] = useState(false);
  const [domainPresets, setDomainPresets] = useState<
    { id: string; label: string }[]
  >([]);
  const [interestSaveState, setInterestSaveState] = useState('');
  const [scanSummary, setScanSummary] = useState<ScanSummary | null>(null);
  const [searchElapsed, setSearchElapsed] = useState(0);
  const [runFeedback, setRunFeedback] = useState<Record<string, RunFeedback>>(
    {},
  );
  const activeScanId = useRef<string | null>(null);
  const archiveRestored = useRef(false);
  const [stopRequested, setStopRequested] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      const result = await api<DashboardStatus>('/api/status');
      setStatus(result);
      if (!archiveRestored.current) {
        if (result.candidate_archive.latest_candidates.length)
          setCandidates(result.candidate_archive.latest_candidates);
        archiveRestored.current = true;
      }
      if (!filtersInitialized) {
        const serverFilters: DiscoveryFilters = {
          domains: result.filters.interests?.domains ?? [],
          keywords: result.filters.interests?.keywords.join(', ') ?? '',
          excluded_keywords:
            result.filters.interests?.excluded_keywords.join(', ') ?? '',
          min_stars: result.filters.min_stars,
          max_stars: result.filters.max_stars,
          languages: result.filters.languages.join(', '),
          max_repo_idle_days: result.filters.max_repo_idle_days,
          max_issue_idle_days: result.filters.max_issue_idle_days,
          max_issue_age_days: result.filters.max_issue_age_days,
          issue_labels: result.filters.issue_labels.join(', '),
          active_work_days: result.filters.active_work_days,
          repo_limit: result.filters.repo_limit,
          issues_per_repo: result.filters.issues_per_repo,
          limit: 12,
          sort_mode: result.filters.discovery_sort,
        };
        setFilters(loadSavedFilters(serverFilters));
        setDomainPresets(result.domain_presets ?? []);
        setFiltersInitialized(true);
      }
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '本地服务未连接',
      });
    }
  }, [filtersInitialized]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refreshStatus(), 0);
    return () => window.clearTimeout(timer);
  }, [refreshStatus]);

  useEffect(() => {
    if (!filtersInitialized) return;
    try {
      window.localStorage.setItem(filterStorageKey, JSON.stringify(filters));
    } catch {
      // Browsers can disable storage; filtering should still work for this page.
    }
  }, [filters, filtersInitialized]);

  useEffect(() => {
    if (!filtersInitialized) return;
    let stale = false;
    setInterestSaveState('保存中…');
    const timer = window.setTimeout(() => {
      void api('/api/interests', {
        method: 'POST',
        body: JSON.stringify({
          domains: filters.domains,
          keywords: filters.keywords,
          excluded_keywords: filters.excluded_keywords,
        }),
      })
        .then(() => {
          if (!stale)
            setInterestSaveState('已保存到本地，命令行与每日任务共用');
        })
        .catch(() => {
          if (!stale)
            setInterestSaveState(
              '保存失败，请检查本地服务；本次搜索仍使用页面设置',
            );
        });
    }, 500);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [
    filters.domains,
    filters.keywords,
    filters.excluded_keywords,
    filtersInitialized,
  ]);

  useEffect(() => {
    if (busy?.kind !== 'discover') return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setSearchElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [busy?.kind]);

  const discoverIssues = async () => {
    const scanId = window.crypto.randomUUID();
    activeScanId.current = scanId;
    setStopRequested(false);
    setSearchElapsed(0);
    setBusy({ kind: 'discover' });
    setNotice({
      tone: 'info',
      text: `会持续扫描 GitHub 的可搜索范围，直到找到 ${filters.limit} 个候选；接近 API 配额上限时会自动等待恢复。`,
    });
    try {
      const result = await api<{
        candidates: Candidate[];
        scan: ScanSummary;
        candidate_archive: CandidateArchive;
      }>('/api/discover', {
        method: 'POST',
        body: JSON.stringify({
          ...filters,
          interests: {
            domains: filters.domains,
            keywords: filters.keywords,
            excluded_keywords: filters.excluded_keywords,
          },
          scan_id: scanId,
          languages: filters.languages
            .split(',')
            .map((language) => language.trim())
            .filter(Boolean),
          issue_labels: filters.issue_labels
            .split(',')
            .map((label) => label.trim())
            .filter(Boolean),
        }),
      });
      setCandidates(result.candidates);
      setScanSummary(result.scan);
      setStatus((current) =>
        current
          ? {
              ...current,
              discovery_next_page: result.scan.next_page,
              candidate_archive: result.candidate_archive,
            }
          : current,
      );
      const found = result.candidates.length;
      setNotice({
        tone: result.scan.target_reached ? 'success' : 'info',
        text: result.scan.target_reached
          ? `已找满 ${found} 个候选，共扫描 ${result.scan.pages.length} 批仓库，核验 ${result.scan.issues_verified} 个 Issue。`
          : result.scan.cancelled
            ? `已停止扫描，保留当前找到的 ${found}/${result.scan.target_count} 个候选。`
            : result.scan.search_exhausted
              ? `GitHub 当前条件下的可搜索范围已经全部扫完，共找到 ${found}/${result.scan.target_count} 个候选。`
              : `扫描达到自定义安全上限，共找到 ${found}/${result.scan.target_count} 个候选。`,
      });
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '候选搜索失败',
      });
    } finally {
      if (activeScanId.current === scanId) {
        activeScanId.current = null;
        setStopRequested(false);
        setBusy(null);
      }
    }
  };

  const stopDiscovery = async () => {
    const scanId = activeScanId.current;
    if (!scanId || stopRequested) return;
    setStopRequested(true);
    setNotice({
      tone: 'info',
      text: '正在安全停止扫描，稍后会返回已找到的候选。',
    });
    try {
      await api('/api/discover/cancel', {
        method: 'POST',
        body: JSON.stringify({ scan_id: scanId }),
      });
    } catch (error) {
      setStopRequested(false);
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '无法停止扫描',
      });
    }
  };

  const connectGitHub = async () => {
    setBusy({ kind: 'connect' });
    const authWindow = window.open('about:blank', 'issuepilot-github-auth');
    try {
      const result = await api<{
        status: string;
        verification_url?: string;
        device_code?: string;
        code_copied?: boolean;
      }>('/api/github/connect', { method: 'POST', body: '{}' });
      if (result.status === 'connected') {
        authWindow?.close();
        setGitHubDeviceCode(null);
        setNotice({ tone: 'success', text: 'GitHub 账号已经连接。' });
      } else {
        setGitHubDeviceCode(result.device_code || null);
        if (authWindow && result.verification_url)
          authWindow.location.href = result.verification_url;
        setNotice({
          tone: 'info',
          text: result.code_copied
            ? 'GitHub 设备登录页已打开，一次性验证码已复制。请在新页面粘贴验证码并授权，然后回来检查连接。'
            : 'GitHub 设备登录页已打开。完成授权后，回来检查连接。',
        });
      }
    } catch (error) {
      authWindow?.close();
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '无法启动 GitHub 连接',
      });
    } finally {
      setBusy(null);
    }
  };

  const runCandidate = async (candidate: Candidate) => {
    setBusy({ kind: 'run', issue: candidate.url });
    setRunFeedback((current) => ({
      ...current,
      [candidate.url]: {
        tone: 'info',
        text: '正在准备代码并启动 Codex 分析。网络下载失败时会自动重试，请保持本页开启。',
      },
    }));
    setNotice({
      tone: 'info',
      text: `AI 正在分析 ${candidate.repository} #${candidate.issue}。请保持本页开启。`,
    });
    try {
      const result = await api<Outcome>('/api/run', {
        method: 'POST',
        body: JSON.stringify({ issue: candidate.url }),
      });
      setNotice({
        tone: result.status === 'prepared' ? 'success' : 'info',
        text:
          result.status === 'prepared'
            ? '修复已在本地准备好；请先审核，再决定是否发布。'
            : result.message,
      });
      setRunFeedback((current) => ({
        ...current,
        [candidate.url]: {
          tone: result.status === 'prepared' ? 'success' : 'info',
          text:
            result.status === 'prepared'
              ? 'AI 分析完成，修复已在本地准备好。请在右侧审核后再决定是否发布。'
              : result.message,
        },
      }));
      await refreshStatus();
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : 'AI 分析失败',
      });
      setRunFeedback((current) => ({
        ...current,
        [candidate.url]: {
          tone: 'error',
          text: `分析未开始或未完成：${error instanceof Error ? error.message : 'AI 分析失败'}`,
        },
      }));
    } finally {
      setBusy(null);
    }
  };

  const publishPrepared = async () => {
    if (!publishTarget || publishText !== '发布') return;
    setBusy({ kind: 'publish', issue: publishTarget.issue });
    setPublishTarget(null);
    setPublishText('');
    setNotice({
      tone: 'info',
      text: '正在提交已审核的同一份修改，并创建 Draft PR。',
    });
    try {
      const result = await api<Outcome>('/api/publish', {
        method: 'POST',
        body: JSON.stringify({
          issue: publishTarget.issue,
          confirmation: 'PUBLISH',
        }),
      });
      setNotice({
        tone: 'success',
        text: result.pull_request
          ? `Draft PR 已创建：${result.pull_request}`
          : result.message,
      });
      await refreshStatus();
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '发布失败',
      });
    } finally {
      setBusy(null);
    }
  };

  const archivePrepared = async (manifest: Manifest) => {
    if (
      !window.confirm(
        `确认归档 ${issueName(manifest.issue)} 的本地工作区？归档可以恢复，不会直接删除。`,
      )
    )
      return;
    setBusy({ kind: 'archive', issue: manifest.issue });
    try {
      await api('/api/archive', {
        method: 'POST',
        body: JSON.stringify({
          issue: manifest.issue,
          confirmation: 'ARCHIVE',
        }),
      });
      setNotice({ tone: 'success', text: '本地工作区和准备记录已安全归档。' });
      await refreshStatus();
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '归档失败',
      });
    } finally {
      setBusy(null);
    }
  };

  const activeStep = useMemo(() => {
    if (!busy) return 0;
    if (busy.kind === 'discover') return 0;
    if (busy.kind === 'run') return 1;
    if (busy.kind === 'publish') return 3;
    return 0;
  }, [busy]);

  const filterLabel = `Stars ${filters.min_stars.toLocaleString()}–${filters.max_stars.toLocaleString()} · Issue 存在不超过 ${filters.max_issue_age_days} 天 · ${sortModeLabels[filters.sort_mode]}`;

  const discoveryStage =
    searchElapsed < 5
      ? '正在获取新的高星仓库批次'
      : searchElapsed < 15
        ? '正在并行收集开放 Issue'
        : searchElapsed < 30
          ? '正在核验关联 PR、认领和已解决信号'
          : `候选不足，正在继续扫描直到找满 ${filters.limit} 个`;

  const flowSteps = [
    ['规则初筛', '轮换仓库批次并核验占用状态'],
    ['AI 分析', '复现问题并实现最小修改'],
    ['验证改动', '执行测试与安全策略检查'],
    ['准备 PR', '审核确认后才创建 Draft PR'],
  ];

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid min-h-screen max-w-[1680px] grid-cols-1 xl:grid-cols-[264px_minmax(0,1fr)]">
        <aside className="hidden flex-col border-r border-white/8 bg-[#101c18] px-5 py-6 text-[#e8f1eb] xl:flex">
          <div className="flex items-center gap-3 px-2">
            <div className="grid size-10 place-items-center rounded-xl bg-[#baf56c] text-[#101c18] shadow-[0_0_0_5px_rgb(186_245_108/8%)]">
              <CircleDot className="size-5" />
            </div>
            <div>
              <div className="text-[15px] font-semibold tracking-tight">
                IssuePilot
              </div>
              <div className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.18em] text-[#91a098]">
                Autonomous lab
              </div>
            </div>
          </div>
          <nav className="mt-10 space-y-1" aria-label="主要导航">
            <a className="nav-item nav-item-active" href="#workspace">
              <LayoutDashboard /> 工作台
            </a>
            <a className="nav-item" href="#candidates">
              <Radar /> 候选池{' '}
              <span className="ml-auto rounded-full bg-white/8 px-2 font-mono text-[10px]">
                {candidates.length}
              </span>
            </a>
            <a className="nav-item" href="#runs">
              <Bot /> 执行记录{' '}
              <span className="ml-auto rounded-full bg-white/8 px-2 font-mono text-[10px]">
                {status?.prepared.length || 0}
              </span>
            </a>
            <button
              className="nav-item w-full"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings2 /> 安全设置
            </button>
          </nav>
          <div className="mt-auto rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <ShieldCheck className="size-4 text-[#baf56c]" /> 安全模式已开启
            </div>
            <p className="mt-2 text-xs leading-5 text-[#9caaa3]">
              AI 可以准备代码和 PR 草稿，但发布前必须由你确认。
            </p>
            <div className="mt-4 flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-[#baf56c]">
              <span className="size-1.5 rounded-full bg-current shadow-[0_0_8px_currentColor]" />{' '}
              Local only
            </div>
          </div>
        </aside>

        <section className="min-w-0 px-4 pb-10 pt-4 sm:px-7 lg:px-10 lg:pt-7">
          <header className="flex items-center justify-between gap-4 border-b border-border pb-5">
            <div className="flex items-center gap-3 xl:hidden">
              <div className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground">
                <CircleDot className="size-4" />
              </div>
              <span className="font-semibold">IssuePilot</span>
            </div>
            <div className="hidden sm:block">
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                Local workspace
              </p>
              <h1 className="mt-1 text-2xl font-semibold tracking-[-0.035em]">
                开源修复工作台
              </h1>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Badge
                variant="outline"
                className="h-8 gap-1.5 rounded-lg bg-card px-3 text-xs"
              >
                <GitFork className="size-3.5" />{' '}
                {status?.github_authenticated
                  ? `@${status.github_user}`
                  : 'GitHub 只读'}
              </Badge>
              <Button
                variant="outline"
                size="lg"
                className="bg-card"
                onClick={() => setSettingsOpen(true)}
              >
                <KeyRound data-icon="inline-start" />{' '}
                {status?.github_authenticated ? '账号设置' : '连接账号'}
              </Button>
            </div>
          </header>

          {notice && (
            <output
              className={`mt-5 flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm ${notice.tone === 'error' ? 'border-red-200 bg-red-50 text-red-800' : notice.tone === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-[#d2dec8] bg-[#f3f8ed] text-[#385245]'}`}
            >
              {notice.tone === 'error' ? (
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
              ) : notice.tone === 'success' ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
              ) : (
                <Loader2
                  className={`mt-0.5 size-4 shrink-0 ${busy ? 'animate-spin' : ''}`}
                />
              )}
              <span className="min-w-0 flex-1 break-words">{notice.text}</span>
              <button
                className="text-xs font-medium underline underline-offset-2"
                onClick={() => setNotice(null)}
              >
                关闭
              </button>
            </output>
          )}

          <div
            id="workspace"
            className="mt-7 grid gap-6 2xl:grid-cols-[minmax(0,1fr)_350px]"
          >
            <div className="min-w-0 space-y-6">
              <section className="scan-panel overflow-hidden rounded-[22px] border border-[#cfd7ce] bg-[#f4f8ef] p-5 shadow-[0_16px_50px_rgb(28_52_42/6%)] sm:p-7">
                <div className="flex flex-col justify-between gap-5 md:flex-row md:items-start">
                  <div>
                    <div className="mb-3 flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-[#4d6a59]">
                      <Sparkles className="size-3.5 text-[#4b7c4a]" /> Verified
                      Issue Discovery
                    </div>
                    <h2 className="max-w-2xl text-2xl font-semibold leading-tight tracking-[-0.035em] sm:text-[32px]">
                      找到值得修、
                      <br className="hidden sm:block" />
                      也适合 AI 修的 Issue
                    </h2>
                    <p className="mt-3 max-w-xl text-sm leading-6 text-[#5d6a62]">
                      先用规则快速轮换筛选并核验占用状态，再由 Codex
                      阅读代码和尝试修复；发布前仍会停下来让你确认。
                    </p>
                  </div>
                  <div className="shrink-0 rounded-2xl border border-[#cfddc7] bg-white/65 p-3.5 text-right">
                    <div className="font-mono text-[10px] uppercase tracking-widest text-[#647168]">
                      本地记录
                    </div>
                    <div className="mt-1 text-xl font-semibold tabular-nums">
                      {status?.prepared.length ?? '—'}
                    </div>
                    <div className="text-[11px] text-[#6d786f]">
                      个准备 / 发布任务
                    </div>
                  </div>
                </div>
                <div className="mt-7 rounded-2xl border border-[#d3ddd0] bg-white/80 p-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-[#344a3d]">
                    <Search className="size-4" /> 筛选范围
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    <label
                      htmlFor="filter-stars"
                      className="text-xs font-medium text-[#526157]"
                    >
                      最低 Stars
                      <Input
                        id="filter-stars"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={1000000}
                        value={filters.min_stars}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            min_stars: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-max-stars"
                      className="text-xs font-medium text-[#526157]"
                    >
                      最高 Stars
                      <Input
                        id="filter-max-stars"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={1000000}
                        value={filters.max_stars}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            max_stars: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-sort-mode"
                      className="text-xs font-medium text-[#526157]"
                    >
                      筛选模式
                      <select
                        id="filter-sort-mode"
                        className="mt-1.5 flex h-9 w-full rounded-md border border-input bg-white px-3 py-1 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                        value={filters.sort_mode}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            sort_mode: event.target.value as DiscoverySortMode,
                          }))
                        }
                      >
                        {Object.entries(sortModeLabels).map(
                          ([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label
                      htmlFor="filter-languages"
                      className="text-xs font-medium text-[#526157] sm:col-span-2"
                    >
                      编程语言（用逗号分隔）
                      <Input
                        id="filter-languages"
                        className="mt-1.5 bg-white"
                        value={filters.languages}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            languages: event.target.value,
                          }))
                        }
                        placeholder="Python, TypeScript, Go"
                      />
                    </label>
                    <fieldset className="text-sm font-medium text-[#526157] sm:col-span-2">
                      <legend>我熟悉的领域</legend>
                      <div
                        className="my-3 flex flex-wrap gap-2"
                        role="group"
                        aria-label="领域多选"
                      >
                        {domainPresets.map(({ id, label }) => (
                          <Button
                            key={id}
                            type="button"
                            variant={
                              filters.domains.includes(id)
                                ? 'default'
                                : 'outline'
                            }
                            aria-pressed={filters.domains.includes(id)}
                            onClick={() =>
                              setFilters((current) => ({
                                ...current,
                                domains: current.domains.includes(id)
                                  ? current.domains.filter(
                                      (value) => value !== id,
                                    )
                                  : [...current.domains, id],
                              }))
                            }
                          >
                            {label}
                          </Button>
                        ))}
                      </div>
                      <label htmlFor="filter-keywords">
                        自定义领域关键词（逗号分隔，可留空）
                      </label>
                      <Input
                        id="filter-keywords"
                        className="mt-1.5 bg-white"
                        value={filters.keywords}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            keywords: event.target.value,
                          }))
                        }
                        placeholder="例如：transformers, inference, 智能体"
                      />
                      <p className="mt-2 font-normal leading-6">
                        按仓库名、简介和 Topics
                        匹配；领域或关键词命中任意一项即可。不选领域且不填关键词时不限领域，其他筛选仍生效。元数据匹配不代表
                        Issue 一定好修。
                      </p>
                    </fieldset>
                    <label
                      htmlFor="filter-excluded-keywords"
                      className="text-sm font-medium text-[#526157] sm:col-span-2"
                    >
                      不想处理的领域关键词（排除优先）
                      <Input
                        id="filter-excluded-keywords"
                        className="mt-1.5 bg-white"
                        value={filters.excluded_keywords}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            excluded_keywords: event.target.value,
                          }))
                        }
                        placeholder="例如：trading, blockchain"
                      />
                      <p className="mt-2 font-normal" role="status">
                        {interestSaveState}
                      </p>
                    </label>
                    <label
                      htmlFor="filter-repo-days"
                      className="text-xs font-medium text-[#526157]"
                    >
                      仓库最近活跃天数
                      <Input
                        id="filter-repo-days"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={3650}
                        value={filters.max_repo_idle_days}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            max_repo_idle_days: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-issue-days"
                      className="text-xs font-medium text-[#526157]"
                    >
                      Issue 最近更新天数
                      <Input
                        id="filter-issue-days"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={3650}
                        value={filters.max_issue_idle_days}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            max_issue_idle_days: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-issue-age-days"
                      className="text-xs font-medium text-[#526157]"
                    >
                      Issue 最长存在天数
                      <Input
                        id="filter-issue-age-days"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={3650}
                        value={filters.max_issue_age_days}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            max_issue_age_days: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-work-days"
                      className="text-xs font-medium text-[#526157]"
                    >
                      近期工作信号窗口
                      <Input
                        id="filter-work-days"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={365}
                        value={filters.active_work_days}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            active_work_days: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-repos"
                      className="text-xs font-medium text-[#526157]"
                    >
                      扫描仓库数（最多 25）
                      <Input
                        id="filter-repos"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={25}
                        value={filters.repo_limit}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            repo_limit: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-issues"
                      className="text-xs font-medium text-[#526157]"
                    >
                      每仓库检查 Issue
                      <Input
                        id="filter-issues"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={100}
                        value={filters.issues_per_repo}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            issues_per_repo: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label
                      htmlFor="filter-limit"
                      className="text-xs font-medium text-[#526157]"
                    >
                      最终候选数量
                      <Input
                        id="filter-limit"
                        className="mt-1.5 bg-white"
                        type="number"
                        min={1}
                        max={50}
                        value={filters.limit}
                        onChange={(event) =>
                          setFilters((current) => ({
                            ...current,
                            limit: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                  </div>
                  <div className="mt-4 flex flex-col gap-3 border-t border-[#dce5d9] pt-3 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1 text-xs text-[#66746b]">
                      <div className="truncate">{filterLabel}</div>
                      <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-[#7a877f]">
                        下一次从仓库批次{' '}
                        {scanSummary?.next_page ??
                          status?.discovery_next_page ??
                          1}{' '}
                        开始 · 持续扫描直到找满或 GitHub 可搜索范围耗尽
                      </div>
                      <div className="mt-1 text-[11px] text-[#66746b]">
                        筛选设置会自动保存，刷新页面后继续使用。
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      {busy?.kind === 'discover' && (
                        <Button
                          size="lg"
                          variant="outline"
                          className="h-11 rounded-xl bg-white"
                          onClick={stopDiscovery}
                          disabled={stopRequested}
                        >
                          {stopRequested ? (
                            <Loader2
                              className="animate-spin"
                              data-icon="inline-start"
                            />
                          ) : null}
                          {stopRequested ? '正在停止' : '停止扫描'}
                        </Button>
                      )}
                      <Button
                        size="lg"
                        className="h-11 rounded-xl bg-[#172b22] px-5 text-white hover:bg-[#29463a]"
                        onClick={discoverIssues}
                        disabled={Boolean(busy)}
                      >
                        {busy?.kind === 'discover' ? (
                          <Loader2
                            className="animate-spin"
                            data-icon="inline-start"
                          />
                        ) : (
                          <Radar data-icon="inline-start" />
                        )}{' '}
                        {busy?.kind === 'discover'
                          ? '正在寻找'
                          : candidates.length
                            ? '按条件重找'
                            : '开始寻找'}
                      </Button>
                    </div>
                  </div>
                  {busy?.kind === 'discover' && (
                    <div className="mt-3 flex items-center justify-between rounded-xl border border-[#d4dfcf] bg-[#f7faf4] px-3.5 py-2.5 text-xs text-[#486052]">
                      <span className="flex items-center gap-2">
                        <Loader2 className="size-3.5 animate-spin" />
                        {discoveryStage}
                      </span>
                      <span className="font-mono tabular-nums">
                        {searchElapsed}s
                      </span>
                    </div>
                  )}
                  {!busy && scanSummary && (
                    <div className="mt-3 rounded-xl bg-[#eef4ea] px-3.5 py-2.5 text-[11px] leading-5 text-[#52675a]">
                      本轮扫描 {scanSummary.repositories_scanned} 个仓库，收集{' '}
                      {scanSummary.issues_collected} 个 Issue，其中{' '}
                      {scanSummary.issues_filtered_by_age}{' '}
                      个因存在时间过长被过滤；深度核验{' '}
                      {scanSummary.issues_verified} 个；
                      {scanSummary.star_windows_scanned > 1
                        ? `已跨越 ${scanSummary.star_windows_scanned} 个 Stars 区间；`
                        : ''}
                      {scanSummary.target_reached
                        ? `已找满 ${scanSummary.target_count} 个候选。`
                        : scanSummary.cancelled
                          ? `你已停止扫描，当前保留 ${candidates.length} 个候选。`
                          : scanSummary.search_exhausted
                            ? `GitHub 可搜索范围已耗尽，未找满 ${scanSummary.target_count} 个。`
                            : `已达到自定义扫描上限，未找满 ${scanSummary.target_count} 个。`}
                      下次从第 {scanSummary.next_page} 批继续。
                    </div>
                  )}
                </div>
              </section>

              <section
                id="candidates"
                className="rounded-[22px] border border-border bg-card p-5 sm:p-6"
              >
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <p className="eyebrow">Ranked candidates</p>
                    <h2 className="mt-1 text-lg font-semibold tracking-tight">
                      候选 Issue
                    </h2>
                  </div>
                  {candidates.length > 0 && (
                    <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      {sortModeLabels[filters.sort_mode]} · {candidates.length}{' '}
                      个
                    </span>
                  )}
                </div>
                {status?.candidate_archive.total_candidates ? (
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    本地已记住 {status.candidate_archive.total_candidates}{' '}
                    个候选 · {status.candidate_archive.run_count} 次扫描记录
                  </p>
                ) : null}
                {candidates.length === 0 ? (
                  <div className="mt-5 grid min-h-48 place-items-center rounded-2xl border border-dashed border-border bg-[#fbfcfa] px-6 text-center">
                    <div>
                      <div className="mx-auto grid size-10 place-items-center rounded-full bg-muted">
                        <Radar className="size-4 text-muted-foreground" />
                      </div>
                      <h3 className="mt-3 text-sm font-semibold">
                        {scanSummary ? '本轮暂无合适候选' : '尚未开始搜索'}
                      </h3>
                      <p className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">
                        {scanSummary
                          ? `已经扫完所有可用批次，但当前条件下没有合适 Issue。请放宽 Stars、时间或标签条件。`
                          : '点击“开始寻找”，IssuePilot 会读取公开 GitHub 数据，不会修改任何仓库。'}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="mt-5 space-y-3">
                    {candidates.map((candidate, index) => (
                      <article
                        key={candidate.url}
                        className="rounded-2xl border border-border bg-[#fbfcfa] p-5"
                      >
                        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <Badge
                                className={
                                  candidate.fit === 'recommended'
                                    ? 'bg-[#dff2d8] text-[#315e38]'
                                    : candidate.fit === 'caution'
                                      ? 'bg-amber-100 text-amber-800'
                                      : ''
                                }
                                variant="secondary"
                              >
                                {candidate.fit === 'recommended'
                                  ? '优先考虑'
                                  : candidate.fit === 'caution'
                                    ? '谨慎处理'
                                    : '建议复核'}
                              </Badge>
                              <span className="font-mono font-semibold text-foreground">
                                {candidate.repository} #{candidate.issue}
                              </span>
                              <span className="flex items-center gap-1">
                                <Star className="size-3 fill-current text-[#b17b18]" />{' '}
                                {formatStars(candidate.stars)}
                              </span>
                              {candidate.language && (
                                <Badge
                                  variant="secondary"
                                  className="bg-[#e9f2e7] text-[#34513c]"
                                >
                                  {candidate.language}
                                </Badge>
                              )}
                              {candidate.labels.slice(0, 3).map((label) => (
                                <Badge key={label} variant="outline">
                                  {label}
                                </Badge>
                              ))}
                            </div>
                            <h3 className="mt-3 text-[17px] font-semibold leading-6 tracking-tight">
                              {candidate.title}
                            </h3>
                            {candidate.summary && (
                              <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted-foreground">
                                {candidate.summary}
                              </p>
                            )}
                            <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                              <span>
                                <b className="text-[#35673e]">
                                  {Math.round(candidate.score)}
                                </b>{' '}
                                可处理分
                              </span>
                              <span>
                                <b className="text-foreground">#{index + 1}</b>{' '}
                                本轮排名
                              </span>
                              <span className="flex items-center gap-1">
                                <MessageSquare className="size-3" />{' '}
                                {candidate.comments} 条讨论
                              </span>
                              <span className="flex items-center gap-1">
                                <Clock3 className="size-3" />{' '}
                                {candidate.age_days == null
                                  ? '创建时间未知'
                                  : `已存在 ${candidate.age_days} 天`}
                              </span>
                              <a
                                href={candidate.url}
                                target="_blank"
                                rel="noreferrer"
                                className="flex items-center gap-1 hover:text-foreground"
                              >
                                查看原 Issue <ExternalLink className="size-3" />
                              </a>
                            </div>
                          </div>
                          <Button
                            size="lg"
                            className="h-11 rounded-xl px-5"
                            onClick={() => runCandidate(candidate)}
                            disabled={Boolean(busy)}
                          >
                            {busy?.kind === 'run' &&
                            busy.issue === candidate.url ? (
                              <Loader2
                                className="animate-spin"
                                data-icon="inline-start"
                              />
                            ) : (
                              <Play data-icon="inline-start" />
                            )}{' '}
                            {busy?.kind === 'run' &&
                            busy.issue === candidate.url
                              ? 'AI 正在处理'
                              : '让 AI 分析'}
                          </Button>
                        </div>
                        {runFeedback[candidate.url] && (
                          <output
                            className={`mt-4 flex items-start gap-2 rounded-xl border px-3.5 py-3 text-xs leading-5 ${runFeedback[candidate.url].tone === 'error' ? 'border-red-200 bg-red-50 text-red-800' : runFeedback[candidate.url].tone === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-[#d4dfcf] bg-[#f1f6ee] text-[#385245]'}`}
                          >
                            {runFeedback[candidate.url].tone === 'error' ? (
                              <AlertCircle className="mt-0.5 size-4 shrink-0" />
                            ) : runFeedback[candidate.url].tone ===
                              'success' ? (
                              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                            ) : (
                              <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin" />
                            )}
                            <span className="min-w-0 break-words">
                              {runFeedback[candidate.url].text}
                            </span>
                          </output>
                        )}
                        <div className="mt-4 grid gap-3 border-t border-border/80 pt-4 md:grid-cols-3">
                          <div className="rounded-xl bg-[#f1f6ee] p-3">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#315b39]">
                              <ShieldCheck className="size-3.5" />{' '}
                              空闲状态已核验
                            </div>
                            <ul className="mt-2 space-y-1 text-[11px] leading-4 text-[#587060]">
                              {candidate.verification.map((item) => (
                                <li key={item}>· {item}</li>
                              ))}
                            </ul>
                          </div>
                          <div className="rounded-xl bg-white p-3">
                            <div className="text-[11px] font-semibold">
                              为什么适合
                            </div>
                            <ul className="mt-2 space-y-1 text-[11px] leading-4 text-muted-foreground">
                              {candidate.fit_reasons.length ? (
                                candidate.fit_reasons.map((item) => (
                                  <li key={item}>· {item}</li>
                                ))
                              ) : (
                                <li>· 需要进入代码库后进一步判断</li>
                              )}
                            </ul>
                            {candidate.risk_reasons.map((item) => (
                              <div
                                key={item}
                                className="mt-1 text-[11px] leading-4 text-amber-700"
                              >
                                ⚠ {item}
                              </div>
                            ))}
                          </div>
                          <div className="rounded-xl bg-white p-3">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold">
                              <GitCommitHorizontal className="size-3.5" />{' '}
                              为什么放了这么久
                            </div>
                            <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
                              {candidate.longevity_reason}
                            </p>
                            {candidate.warnings.map((item) => (
                              <p
                                key={item}
                                className="mt-1 text-[11px] leading-4 text-amber-700"
                              >
                                ⚠ {item}
                              </p>
                            ))}
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </div>

            <aside id="runs" className="space-y-6">
              <section className="rounded-[22px] border border-border bg-card p-5">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="eyebrow">System status</p>
                    <h2 className="mt-1 text-base font-semibold">准备情况</h2>
                  </div>
                  <button
                    aria-label="刷新状态"
                    onClick={refreshStatus}
                    className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <RefreshCw className="size-3.5" />
                  </button>
                </div>
                <div className="mt-5 space-y-3">
                  {[
                    [
                      'GitHub 浏览',
                      status?.github_authenticated
                        ? `@${status.github_user}`
                        : '匿名只读',
                      true,
                    ],
                    [
                      'Codex 分析',
                      status?.codex_authenticated ? '已就绪' : '未登录',
                      Boolean(status?.codex_authenticated),
                    ],
                    [
                      '自动发布',
                      status?.publish_ready ? '已解锁' : '保持锁定',
                      Boolean(status?.publish_ready),
                    ],
                  ].map(([label, value, ready]) => (
                    <div
                      key={String(label)}
                      className="flex items-center justify-between rounded-xl bg-muted/55 px-3 py-2.5 text-xs"
                    >
                      <span className="text-muted-foreground">{label}</span>
                      <span className="flex items-center gap-1.5 font-medium">
                        {ready ? (
                          <CheckCircle2 className="size-3.5 text-[#55965d]" />
                        ) : (
                          <KeyRound className="size-3.5 text-[#a4772b]" />
                        )}
                        {value}
                      </span>
                    </div>
                  ))}
                </div>
              </section>

              <section className="rounded-[22px] border border-border bg-[#17251f] p-5 text-[#edf5ef]">
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#9faea6]">
                  Autonomous flow
                </p>
                <h2 className="mt-1 text-base font-semibold">
                  一次修复会发生什么
                </h2>
                <div className="mt-5 space-y-0">
                  {flowSteps.map(([label, detail], index) => {
                    const running = Boolean(busy) && index === activeStep;
                    const done = Boolean(busy) && index < activeStep;
                    return (
                      <div
                        key={label}
                        className="relative flex gap-3 pb-5 last:pb-0"
                      >
                        {index < flowSteps.length - 1 && (
                          <span className="absolute left-[9px] top-5 h-[calc(100%-5px)] w-px bg-white/12" />
                        )}
                        <span
                          className={`relative z-10 mt-0.5 grid size-5 place-items-center rounded-full border text-[9px] ${running ? 'border-[#baf56c] bg-[#baf56c] text-[#14231c]' : done ? 'border-[#6b9d71] bg-[#31573d] text-white' : 'border-white/20 bg-[#17251f] text-[#829087]'}`}
                        >
                          {running ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : done ? (
                            <CheckCircle2 className="size-3" />
                          ) : (
                            index + 1
                          )}
                        </span>
                        <div>
                          <div className="text-xs font-medium">{label}</div>
                          <div className="mt-1 text-[11px] leading-4 text-[#94a099]">
                            {detail}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="rounded-[22px] border border-border bg-card p-5">
                <div className="flex items-end justify-between gap-3">
                  <div>
                    <p className="eyebrow">Prepared runs</p>
                    <h2 className="mt-1 text-base font-semibold">
                      待审核与 PR
                    </h2>
                  </div>
                  {status?.prepared.length ? (
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {status.prepared.length}
                    </span>
                  ) : null}
                </div>
                <div className="mt-4 space-y-3">
                  {!status?.prepared.length ? (
                    <div className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-xs leading-5 text-muted-foreground">
                      AI 完成一次可用修复后，会在这里等待你的审核。
                    </div>
                  ) : (
                    status.prepared.map((manifest) => (
                      <article
                        key={manifest.issue}
                        className="rounded-xl border border-border bg-[#fbfcfa] p-3.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="truncate font-mono text-[11px] font-semibold">
                              {issueName(manifest.issue)}
                            </div>
                            <Badge
                              variant={
                                manifest.status === 'published'
                                  ? 'secondary'
                                  : 'outline'
                              }
                              className="mt-2"
                            >
                              {manifest.status === 'published'
                                ? '已发布'
                                : manifest.status === 'prepared'
                                  ? '待审核'
                                  : manifest.status}
                            </Badge>
                          </div>
                          {manifest.pull_request && (
                            <a
                              aria-label={`打开 ${issueName(manifest.issue)} 的 Pull Request`}
                              href={manifest.pull_request}
                              target="_blank"
                              rel="noreferrer"
                              className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"
                            >
                              <ExternalLink className="size-3.5" />
                            </a>
                          )}
                        </div>
                        <div className="mt-3 flex gap-2">
                          {manifest.status !== 'published' && (
                            <Button
                              size="sm"
                              className="flex-1"
                              disabled={!status.publish_ready || Boolean(busy)}
                              onClick={() => setPublishTarget(manifest)}
                            >
                              <Send data-icon="inline-start" /> 发布 Draft PR
                            </Button>
                          )}
                          <Button
                            size="icon-sm"
                            variant="ghost"
                            aria-label="归档"
                            disabled={Boolean(busy)}
                            onClick={() => archivePrepared(manifest)}
                          >
                            <Archive />
                          </Button>
                        </div>
                        {manifest.status !== 'published' &&
                          !status.publish_ready && (
                            <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
                              连接 GitHub 并解锁发布后可用
                            </p>
                          )}
                      </article>
                    ))
                  )}
                </div>
              </section>
            </aside>
          </div>
        </section>
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>连接与安全设置</DialogTitle>
            <DialogDescription>
              使用 GitHub 官方网页登录。IssuePilot 不会要求你在网页里粘贴或保存
              Token。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-1 text-sm">
            <div className="rounded-xl border border-border bg-muted/50 p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium">1. 连接 GitHub</span>
                <Badge
                  variant={
                    status?.github_authenticated ? 'secondary' : 'outline'
                  }
                >
                  {status?.github_authenticated
                    ? `@${status.github_user}`
                    : '尚未连接'}
                </Badge>
              </div>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {status?.github_cli_available
                  ? '点击后会生成一次性设备码，并打开 GitHub 官方授权页面。'
                  : '连接账号需要 GitHub 官方命令行工具。先完成安装，再回到这里刷新状态。'}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {status?.github_cli_available ? (
                  <Button
                    size="sm"
                    onClick={connectGitHub}
                    disabled={
                      Boolean(busy) || Boolean(status?.github_authenticated)
                    }
                  >
                    {busy?.kind === 'connect' ? (
                      <Loader2
                        className="animate-spin"
                        data-icon="inline-start"
                      />
                    ) : (
                      <GitFork data-icon="inline-start" />
                    )}
                    {status?.github_authenticated ? '已连接' : '连接 GitHub'}
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    render={
                      <a
                        href="https://cli.github.com/"
                        target="_blank"
                        rel="noreferrer"
                        aria-label="打开 GitHub CLI 安装页面"
                      />
                    }
                  >
                    <ExternalLink data-icon="inline-start" /> 安装 GitHub CLI
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={refreshStatus}>
                  <RefreshCw data-icon="inline-start" />{' '}
                  {status?.github_cli_available ? '检查连接' : '安装好了，刷新'}
                </Button>
              </div>
              {githubDeviceCode && (
                <div className="mt-3 rounded-xl border border-[#b8c9ae] bg-white p-3 text-center">
                  <p className="text-[11px] font-medium text-muted-foreground">
                    在 GitHub 页面输入这个设备码
                  </p>
                  <div className="mt-2 font-mono text-2xl font-bold tracking-[0.16em] text-[#172b22]">
                    {githubDeviceCode}
                  </div>
                  <Button
                    className="mt-3"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      void navigator.clipboard.writeText(githubDeviceCode);
                      setNotice({
                        tone: 'success',
                        text: '设备码已复制。请粘贴到 GitHub 设备登录页。',
                      });
                    }}
                  >
                    复制设备码
                  </Button>
                </div>
              )}
              {!status?.github_cli_available && (
                <p className="mt-2 text-xs text-amber-700">
                  安装前仍可匿名浏览公开 Issue，但搜索额度较低，也无法创建 Draft
                  PR。
                </p>
              )}
            </div>
            <div className="rounded-xl border border-border bg-muted/50 p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium">2. 解锁发布</span>
                <Badge
                  variant={status?.publish_unlocked ? 'secondary' : 'outline'}
                >
                  {status?.publish_unlocked ? '已解锁' : '保持锁定'}
                </Badge>
              </div>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                连接账号不会自动发布。创建 Draft PR
                仍需单独解锁，并在每次发布前再次确认。
              </p>
            </div>
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              完成
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(publishTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setPublishTarget(null);
            setPublishText('');
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>确认创建 Draft PR</DialogTitle>
            <DialogDescription>
              这会使用你的 GitHub 身份 fork 仓库、提交已经验证过的同一份
              diff，并向上游创建草稿 PR。
            </DialogDescription>
          </DialogHeader>
          {publishTarget && (
            <div className="rounded-xl border border-border bg-muted/50 p-3 font-mono text-xs">
              {issueName(publishTarget.issue)}
            </div>
          )}
          <div>
            <label
              className="text-xs font-medium"
              htmlFor="publish-confirmation"
            >
              输入“发布”以确认
            </label>
            <Input
              id="publish-confirmation"
              className="mt-2"
              value={publishText}
              onChange={(event) => setPublishText(event.target.value)}
              autoComplete="off"
            />
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              取消
            </DialogClose>
            <Button onClick={publishPrepared} disabled={publishText !== '发布'}>
              <Send data-icon="inline-start" /> 创建 Draft PR
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}
