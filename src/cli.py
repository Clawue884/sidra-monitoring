"""Command-line interface for DevOps Agent."""

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.live import Live

from .config import settings
from .agents import InfrastructureAgent, DocumentationAgent, MonitoringAgent

app = typer.Typer(
    name="devops-agent",
    help="AI-powered DevOps agent for infrastructure discovery and documentation",
    add_completion=False,
)

console = Console()


def run_async(coro):
    """Run async function in sync context."""
    return asyncio.run(coro)


@app.command()
def discover(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
    format: str = typer.Option("json", "--format", "-f", help="Output format: json, markdown"),
):
    """Perform full infrastructure discovery and analysis."""
    console.print("[bold]Starting infrastructure discovery...[/bold]\n")

    settings.ensure_dirs()
    agent = InfrastructureAgent()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Scanning networks and discovering hosts...", total=None)
        analysis = run_async(agent.full_discovery())

    # Display summary
    console.print("\n[bold green]Discovery Complete![/bold green]\n")

    table = Table(title="Infrastructure Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Total Servers", str(len(analysis.servers)))
    table.add_row("Networks Scanned", str(len(analysis.networks)))
    table.add_row("Docker Hosts", str(len(analysis.docker)))
    table.add_row("Databases Found", str(len(analysis.databases)))
    table.add_row("Health Score", f"{analysis.health_score}/100")

    console.print(table)

    # Show recommendations
    if analysis.recommendations:
        console.print("\n[bold]Recommendations:[/bold]")
        for i, rec in enumerate(analysis.recommendations[:5], 1):
            console.print(f"  {i}. {rec}")

    # Save output
    result = agent.to_dict(analysis)
    if output:
        if format == "json":
            output.write_text(json.dumps(result, indent=2))
        else:
            doc_agent = DocumentationAgent()
            doc = run_async(doc_agent.generate_full_documentation(result, output))
        console.print(f"\n[green]Results saved to: {output}[/green]")
    else:
        default_output = settings.output_dir / "discovery_result.json"
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        default_output.write_text(json.dumps(result, indent=2))
        console.print(f"\n[green]Results saved to: {default_output}[/green]")


@app.command()
def scan(
    host: str = typer.Argument(..., help="Host IP or hostname to scan"),
):
    """Quick scan of a single host."""
    console.print(f"[bold]Scanning host: {host}[/bold]\n")

    agent = InfrastructureAgent()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Scanning {host}...", total=None)
        result = run_async(agent.quick_scan(host))

    if "error" in result:
        console.print(f"[red]Error: {result['error']}[/red]")
        raise typer.Exit(1)

    # Display results
    console.print(Panel(
        f"""[cyan]Hostname:[/cyan] {result.get('hostname', 'N/A')}
[cyan]IP:[/cyan] {result.get('ip_address', 'N/A')}
[cyan]OS:[/cyan] {result.get('os', 'N/A')}
[cyan]Kernel:[/cyan] {result.get('kernel', 'N/A')}
[cyan]Uptime:[/cyan] {result.get('uptime', 'N/A')}

[cyan]CPU:[/cyan] {result.get('cpu', {}).get('cores', 'N/A')} cores, {result.get('cpu', {}).get('usage_percent', 0):.1f}% usage
[cyan]Memory:[/cyan] {result.get('memory', {}).get('total_gb', 0):.1f} GB, {result.get('memory', {}).get('usage_percent', 0):.1f}% usage

[cyan]Docker:[/cyan] {'Installed' if result.get('docker') else 'Not installed'}
[cyan]Open Ports:[/cyan] {', '.join(map(str, result.get('open_ports', [])[:10]))}""",
        title=f"Host: {host}",
        border_style="green",
    ))

    # Show disks
    if result.get("disks"):
        disk_table = Table(title="Disk Usage")
        disk_table.add_column("Mount", style="cyan")
        disk_table.add_column("Size (GB)", justify="right")
        disk_table.add_column("Used (GB)", justify="right")
        disk_table.add_column("Usage %", justify="right")

        for disk in result["disks"][:5]:
            disk_table.add_row(
                disk.get("mount", ""),
                f"{disk.get('total_gb', 0):.1f}",
                f"{disk.get('used_gb', 0):.1f}",
                f"{disk.get('usage_percent', 0):.1f}%",
            )
        console.print(disk_table)


@app.command()
def network(
    cidr: Optional[str] = typer.Argument(None, help="Network CIDR to scan (e.g., 192.168.1.0/24)"),
    quick: bool = typer.Option(False, "--quick", "-q", help="Quick ping scan only"),
):
    """Scan a network for active hosts."""
    from .discovery import NetworkScanner

    networks = [cidr] if cidr else settings.networks_list

    scanner = NetworkScanner()

    for network_cidr in networks:
        console.print(f"\n[bold]Scanning network: {network_cidr}[/bold]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            if quick:
                progress.add_task("Quick ping scan...", total=None)
                hosts = run_async(scanner.quick_ping_scan(network_cidr))
                console.print(f"[green]Found {len(hosts)} live hosts:[/green]")
                for host in hosts:
                    console.print(f"  - {host}")
            else:
                progress.add_task("Full port scan...", total=None)
                result = run_async(scanner.scan_network(network_cidr))

                table = Table(title=f"Hosts in {network_cidr}")
                table.add_column("IP", style="cyan")
                table.add_column("Hostname", style="green")
                table.add_column("SSH", style="yellow")
                table.add_column("Open Ports")

                for host in result.hosts:
                    table.add_row(
                        host.ip,
                        host.hostname or "-",
                        "Yes" if host.ssh_accessible else "No",
                        ", ".join([str(p.port) for p in host.open_ports[:5]]),
                    )

                console.print(table)


@app.command()
def document(
    input_file: Optional[Path] = typer.Option(None, "--input", "-i", help="Input JSON from discovery"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
    discover_first: bool = typer.Option(False, "--discover", "-d", help="Run discovery first"),
):
    """Generate infrastructure documentation."""
    settings.ensure_dirs()

    if discover_first:
        console.print("[bold]Running discovery first...[/bold]\n")
        agent = InfrastructureAgent()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Discovering infrastructure...", total=None)
            analysis = run_async(agent.full_discovery())
            data = agent.to_dict(analysis)
    elif input_file and input_file.exists():
        data = json.loads(input_file.read_text())
    else:
        # Try to find latest discovery result
        default_input = settings.output_dir / "discovery_result.json"
        if default_input.exists():
            data = json.loads(default_input.read_text())
        else:
            console.print("[red]No input data. Run discovery first or provide --input[/red]")
            raise typer.Exit(1)

    console.print("[bold]Generating documentation...[/bold]\n")

    doc_agent = DocumentationAgent()
    output_path = output or (settings.reports_dir / "infrastructure_documentation.md")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Generating documentation with AI...", total=None)
        doc = run_async(doc_agent.generate_full_documentation(data, output_path))

    console.print(f"[green]Documentation saved to: {output_path}[/green]")
    console.print(f"\nDocument length: {len(doc)} characters")


@app.command()
def monitor(
    hosts: Optional[str] = typer.Option(None, "--hosts", "-h", help="Comma-separated list of hosts"),
    interval: int = typer.Option(60, "--interval", "-i", help="Check interval in seconds"),
    duration: int = typer.Option(0, "--duration", "-d", help="Duration in minutes (0 = infinite)"),
):
    """Start continuous monitoring of hosts."""
    host_list = hosts.split(",") if hosts else []

    if not host_list:
        # Auto-discover hosts
        console.print("[yellow]No hosts specified. Running quick discovery...[/yellow]")
        from .discovery import NetworkScanner
        scanner = NetworkScanner()

        for cidr in settings.networks_list:
            live = run_async(scanner.quick_ping_scan(cidr))
            host_list.extend(live)

    if not host_list:
        console.print("[red]No hosts found to monitor[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Starting monitoring for {len(host_list)} hosts[/bold]")
    console.print(f"Interval: {interval}s")
    console.print("Press Ctrl+C to stop\n")

    monitor_agent = MonitoringAgent(hosts=host_list, interval=interval)

    # Add console callback for alerts
    def alert_callback(alert):
        color = "red" if alert.severity == "critical" else "yellow"
        console.print(f"[{color}]ALERT [{alert.severity}] {alert.host}: {alert.message}[/{color}]")

    monitor_agent.add_alert_callback(alert_callback)

    try:
        if duration > 0:
            async def run_for_duration():
                task = asyncio.create_task(monitor_agent.start())
                await asyncio.sleep(duration * 60)
                await monitor_agent.stop()

            run_async(run_for_duration())
        else:
            run_async(monitor_agent.start())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping monitoring...[/yellow]")
        run_async(monitor_agent.stop())

    # Show final summary
    summary = monitor_agent.get_status_summary()
    console.print("\n[bold]Monitoring Summary:[/bold]")
    console.print(f"  Healthy: {summary['hosts_healthy']}")
    console.print(f"  Degraded: {summary['hosts_degraded']}")
    console.print(f"  Unhealthy: {summary['hosts_unhealthy']}")
    console.print(f"  Unreachable: {summary['hosts_unreachable']}")
    console.print(f"  Active Alerts: {summary['active_alerts']}")


@app.command()
def status():
    """Show current monitoring status."""
    # Load status from file if exists
    status_file = settings.output_dir / "monitoring_status.json"
    if status_file.exists():
        status = json.loads(status_file.read_text())
        console.print_json(json.dumps(status, indent=2))
    else:
        console.print("[yellow]No monitoring status available. Run 'monitor' first.[/yellow]")


@app.command()
def report(
    type: str = typer.Option("daily", "--type", "-t", help="Report type: daily, weekly, full"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
):
    """Generate infrastructure report."""
    settings.ensure_dirs()

    # Load discovery data
    discovery_file = settings.output_dir / "discovery_result.json"
    if not discovery_file.exists():
        console.print("[yellow]No discovery data. Running discovery first...[/yellow]")
        agent = InfrastructureAgent()
        analysis = run_async(agent.full_discovery())
        data = agent.to_dict(analysis)
        discovery_file.write_text(json.dumps(data, indent=2))
    else:
        data = json.loads(discovery_file.read_text())

    doc_agent = DocumentationAgent()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        if type == "daily":
            progress.add_task("Generating daily report...", total=None)
            report_content = run_async(doc_agent.generate_daily_report(data))
        else:
            progress.add_task("Generating full report...", total=None)
            report_content = run_async(doc_agent.generate_full_documentation(data))

    if output:
        output.write_text(report_content)
        console.print(f"[green]Report saved to: {output}[/green]")
    else:
        console.print(Panel(report_content, title=f"{type.title()} Report"))


@app.command()
def api(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="API host"),
    port: int = typer.Option(8200, "--port", "-p", help="API port"),
):
    """Start the DevOps Agent API server."""
    import uvicorn
    console.print(f"[bold]Starting API server on {host}:{port}[/bold]")
    uvicorn.run("src.api.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    app()
