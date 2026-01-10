"""
LLM Analyzer.

Uses local LLMs (Ollama) to analyze logs, correlate alerts,
and generate human-readable reports.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Result of LLM analysis."""
    analysis_type: str
    summary: str
    severity: str  # info, warning, critical
    details: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class DailyReport:
    """Daily infrastructure report."""
    date: str
    health_score: int  # 0-100
    summary: str
    critical_issues: list[str]
    warnings: list[str]
    resource_usage: dict
    recommendations: list[str]
    generated_at: float = field(default_factory=time.time)


class LLMAnalyzer:
    """
    LLM-powered infrastructure analyzer.

    Uses Ollama to:
    - Detect anomalies in logs
    - Correlate related alerts
    - Generate human-readable summaries
    - Provide actionable recommendations
    """

    def __init__(
        self,
        ollama_host: str = "http://localhost:11434",
        model: str = "devstral",
        fast_model: str = "nemotron-3-nano",
    ):
        """Initialize the LLM analyzer."""
        self.ollama_host = ollama_host.rstrip('/')
        self.model = model
        self.fast_model = fast_model
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=120)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _generate(self, prompt: str, model: Optional[str] = None, temperature: float = 0.3) -> str:
        """Generate text using Ollama."""
        try:
            session = await self._get_session()
            payload = {
                "model": model or self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                }
            }

            async with session.post(
                f"{self.ollama_host}/api/generate",
                json=payload,
            ) as response:
                if response.status != 200:
                    logger.error(f"Ollama error: {await response.text()}")
                    return ""

                result = await response.json()
                return result.get("response", "")

        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return ""

    async def analyze_logs(self, logs: list[dict]) -> AnalysisResult:
        """
        Analyze a batch of logs for anomalies and patterns.

        Args:
            logs: List of log entries with level, message, source, host

        Returns:
            Analysis result with summary and recommendations
        """
        if not logs:
            return AnalysisResult(
                analysis_type="logs",
                summary="No logs to analyze",
                severity="info",
            )

        # Prepare log summary for LLM
        log_summary = self._prepare_log_summary(logs)

        prompt = f"""You are a DevOps expert analyzing server logs. Analyze the following log summary and identify:
1. Critical issues requiring immediate attention
2. Warning patterns that may indicate problems
3. Unusual activity or anomalies
4. Security concerns

Log Summary:
{log_summary}

Provide your analysis in the following JSON format:
{{
    "severity": "info|warning|critical",
    "summary": "Brief summary of findings",
    "critical_issues": ["list of critical issues"],
    "warnings": ["list of warnings"],
    "anomalies": ["list of anomalies"],
    "recommendations": ["list of actionable recommendations"]
}}

Respond only with valid JSON."""

        response = await self._generate(prompt, model=self.fast_model)

        try:
            result = json.loads(response)
            return AnalysisResult(
                analysis_type="logs",
                summary=result.get("summary", "Analysis complete"),
                severity=result.get("severity", "info"),
                details={
                    "critical_issues": result.get("critical_issues", []),
                    "warnings": result.get("warnings", []),
                    "anomalies": result.get("anomalies", []),
                },
                recommendations=result.get("recommendations", []),
            )
        except json.JSONDecodeError:
            return AnalysisResult(
                analysis_type="logs",
                summary=response[:500] if response else "Analysis failed",
                severity="info",
            )

    async def correlate_alerts(self, alerts: list[dict]) -> AnalysisResult:
        """
        Correlate related alerts to identify root causes.

        Args:
            alerts: List of alerts with metric, severity, message, host

        Returns:
            Analysis with correlated incidents
        """
        if not alerts:
            return AnalysisResult(
                analysis_type="alert_correlation",
                summary="No alerts to correlate",
                severity="info",
            )

        # Group alerts by host and time
        alert_text = self._prepare_alert_summary(alerts)

        prompt = f"""You are a DevOps expert analyzing infrastructure alerts. Group related alerts and identify:
1. Root cause analysis - what's causing these alerts?
2. Impact assessment - what systems are affected?
3. Priority ranking - which issues need immediate attention?

Alerts:
{alert_text}

Provide your analysis in the following JSON format:
{{
    "severity": "info|warning|critical",
    "summary": "Brief summary of the situation",
    "incidents": [
        {{
            "name": "Incident name",
            "root_cause": "Likely root cause",
            "affected_hosts": ["list of hosts"],
            "related_alerts": ["list of related alert messages"],
            "priority": "critical|high|medium|low"
        }}
    ],
    "recommendations": ["list of immediate actions to take"]
}}

Respond only with valid JSON."""

        response = await self._generate(prompt)

        try:
            result = json.loads(response)
            return AnalysisResult(
                analysis_type="alert_correlation",
                summary=result.get("summary", "Correlation complete"),
                severity=result.get("severity", "warning"),
                details={
                    "incidents": result.get("incidents", []),
                },
                recommendations=result.get("recommendations", []),
            )
        except json.JSONDecodeError:
            return AnalysisResult(
                analysis_type="alert_correlation",
                summary=response[:500] if response else "Correlation failed",
                severity="warning",
            )

    async def generate_daily_report(
        self,
        metrics_summary: dict,
        alerts: list[dict],
        logs_summary: dict,
    ) -> DailyReport:
        """
        Generate a comprehensive daily report.

        Args:
            metrics_summary: Summary of key metrics (CPU, memory, disk)
            alerts: List of alerts from the day
            logs_summary: Summary of log analysis

        Returns:
            Daily report with health score and recommendations
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Calculate health score
        health_score = self._calculate_health_score(metrics_summary, alerts)

        # Prepare context for LLM
        context = f"""Infrastructure Summary for {today}:

Metrics:
- Active hosts: {metrics_summary.get('hosts_up', 'N/A')}
- Average CPU usage: {metrics_summary.get('avg_cpu', 'N/A')}%
- Average memory usage: {metrics_summary.get('avg_memory', 'N/A')}%
- Average disk usage: {metrics_summary.get('avg_disk', 'N/A')}%

Alerts Summary:
- Critical alerts: {len([a for a in alerts if a.get('severity') == 'critical'])}
- High alerts: {len([a for a in alerts if a.get('severity') == 'high'])}
- Warning alerts: {len([a for a in alerts if a.get('severity') == 'warning'])}

Recent critical alerts:
{chr(10).join([f"- {a.get('host', 'unknown')}: {a.get('message', '')}" for a in alerts if a.get('severity') == 'critical'][:5])}

Log Summary:
- Total errors: {logs_summary.get('errors_count', 0)}
- Total warnings: {logs_summary.get('warnings_count', 0)}
"""

        prompt = f"""You are a senior DevOps engineer preparing a daily infrastructure report for the team.

{context}

Generate a professional daily report with:
1. Executive summary (2-3 sentences)
2. Critical issues requiring attention
3. Performance observations
4. Capacity planning notes
5. Actionable recommendations

Write in a clear, concise style suitable for a morning standup.

Provide your report in JSON format:
{{
    "executive_summary": "2-3 sentence overview",
    "critical_issues": ["list of critical issues"],
    "performance_notes": ["notable performance observations"],
    "capacity_notes": ["capacity and trend observations"],
    "recommendations": ["prioritized action items"]
}}

Respond only with valid JSON."""

        response = await self._generate(prompt)

        try:
            result = json.loads(response)
            return DailyReport(
                date=today,
                health_score=health_score,
                summary=result.get("executive_summary", "Report generated"),
                critical_issues=result.get("critical_issues", []),
                warnings=result.get("performance_notes", []) + result.get("capacity_notes", []),
                resource_usage=metrics_summary,
                recommendations=result.get("recommendations", []),
            )
        except json.JSONDecodeError:
            return DailyReport(
                date=today,
                health_score=health_score,
                summary=f"Infrastructure health score: {health_score}/100. {response[:200] if response else 'Report generation incomplete.'}",
                critical_issues=[a.get('message', '') for a in alerts if a.get('severity') == 'critical'][:5],
                warnings=[],
                resource_usage=metrics_summary,
                recommendations=["Review critical alerts", "Check system logs for errors"],
            )

    async def generate_incident_summary(self, incident: dict) -> str:
        """Generate a human-readable incident summary."""
        prompt = f"""Summarize this infrastructure incident for the team:

Incident: {incident.get('name', 'Unknown')}
Severity: {incident.get('severity', 'Unknown')}
Affected hosts: {', '.join(incident.get('hosts', []))}
Alerts: {json.dumps(incident.get('alerts', []))}

Write a brief (3-5 sentences) summary explaining:
1. What happened
2. What's affected
3. What action is needed

Be concise and actionable."""

        return await self._generate(prompt, model=self.fast_model, temperature=0.2)

    def _prepare_log_summary(self, logs: list[dict]) -> str:
        """Prepare a condensed log summary for LLM analysis."""
        # Group by level
        by_level = {'critical': [], 'error': [], 'warning': []}

        for log in logs:
            level = log.get('level', 'info').lower()
            if level in by_level:
                msg = f"[{log.get('host', 'unknown')}] {log.get('message', '')[:200]}"
                by_level[level].append(msg)

        summary_parts = []
        for level, messages in by_level.items():
            if messages:
                summary_parts.append(f"\n{level.upper()} ({len(messages)} entries):")
                for msg in messages[:10]:  # Limit to 10 per level
                    summary_parts.append(f"  - {msg}")

        return "\n".join(summary_parts) if summary_parts else "No significant log entries"

    def _prepare_alert_summary(self, alerts: list[dict]) -> str:
        """Prepare alert summary for correlation."""
        lines = []
        for alert in alerts[:30]:  # Limit to 30 alerts
            lines.append(
                f"[{alert.get('severity', 'unknown').upper()}] "
                f"{alert.get('host', 'unknown')}: "
                f"{alert.get('message', '')}"
            )
        return "\n".join(lines)

    def _calculate_health_score(self, metrics: dict, alerts: list[dict]) -> int:
        """Calculate infrastructure health score (0-100)."""
        score = 100

        # Deduct for high resource usage
        try:
            cpu = float(metrics.get('avg_cpu', 0))
            if cpu > 80:
                score -= 20
            elif cpu > 60:
                score -= 10

            memory = float(metrics.get('avg_memory', 0))
            if memory > 80:
                score -= 20
            elif memory > 60:
                score -= 10

            disk = float(metrics.get('avg_disk', 0))
            if disk > 90:
                score -= 25
            elif disk > 80:
                score -= 15
        except (ValueError, TypeError):
            pass

        # Deduct for alerts
        critical_count = len([a for a in alerts if a.get('severity') == 'critical'])
        high_count = len([a for a in alerts if a.get('severity') == 'high'])

        score -= critical_count * 10
        score -= high_count * 5

        return max(0, min(100, score))

    async def check_health(self) -> bool:
        """Check if Ollama is healthy."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.ollama_host}/api/tags") as response:
                return response.status == 200
        except Exception:
            return False

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()


# Scheduled analyzer for background analysis
class ScheduledAnalyzer:
    """Runs LLM analysis on a schedule."""

    def __init__(
        self,
        analyzer: LLMAnalyzer,
        get_alerts_fn,
        get_logs_fn,
        get_metrics_fn,
        report_callback=None,
    ):
        """Initialize the scheduled analyzer."""
        self.analyzer = analyzer
        self.get_alerts = get_alerts_fn
        self.get_logs = get_logs_fn
        self.get_metrics = get_metrics_fn
        self.report_callback = report_callback
        self._running = False

    async def start(self):
        """Start the scheduled analyzer."""
        self._running = True
        asyncio.create_task(self._run_periodic_analysis())
        asyncio.create_task(self._run_daily_report())

    async def stop(self):
        """Stop the scheduled analyzer."""
        self._running = False

    async def _run_periodic_analysis(self):
        """Run analysis every 5 minutes."""
        while self._running:
            await asyncio.sleep(300)  # 5 minutes

            try:
                # Get recent alerts
                alerts = await self.get_alerts(100)

                if alerts:
                    # Correlate alerts
                    result = await self.analyzer.correlate_alerts(alerts)

                    if result.severity in ('critical', 'warning'):
                        logger.info(f"Alert correlation: {result.summary}")

                        if self.report_callback:
                            await self.report_callback(result)

            except Exception as e:
                logger.error(f"Periodic analysis error: {e}")

    async def _run_daily_report(self):
        """Generate daily report at 8:00 AM."""
        while self._running:
            # Calculate time until 8:00 AM
            now = datetime.now()
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target.replace(day=target.day + 1)

            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if not self._running:
                break

            try:
                # Gather data
                metrics = await self.get_metrics()
                alerts = await self.get_alerts(500)
                logs = await self.get_logs(1000)

                logs_summary = {
                    'errors_count': len([l for l in logs if l.get('level') == 'error']),
                    'warnings_count': len([l for l in logs if l.get('level') == 'warning']),
                }

                # Generate report
                report = await self.analyzer.generate_daily_report(
                    metrics_summary=metrics,
                    alerts=alerts,
                    logs_summary=logs_summary,
                )

                logger.info(f"Daily report generated: Health score {report.health_score}/100")

                if self.report_callback:
                    await self.report_callback(report)

            except Exception as e:
                logger.error(f"Daily report error: {e}")
