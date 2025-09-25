"""Command-line interface for API Documentation Processor."""

import sys
from pathlib import Path
from typing import List

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.text import Text

from .config import Config, create_sample_config, load_config
from .utils import validate_url, deduplicate_urls, validate_urls_batch
from .firecrawl_client import FirecrawlClient
from .llm_client import LLMClient


console = Console()


def print_header():
    """Print the application header."""
    header = Text("API Documentation Processor", style="bold blue")
    subheader = Text("Automate API integration research and planning", style="dim")
    
    console.print()
    console.print(Panel.fit(f"{header}\n{subheader}", border_style="blue"))
    console.print()


def collect_urls() -> List[str]:
    """Interactively collect URLs from user."""
    urls = []
    console.print("[bold]Enter API documentation URLs[/bold]")
    console.print("(Press Enter without typing a URL to finish)")
    console.print()
    
    while True:
        url = click.prompt("URL", default="", show_default=False).strip()
        
        if not url:
            break
        
        is_valid, error = validate_url(url)
        if not is_valid:
            console.print(f"[red]Invalid URL:[/red] {error}")
            continue
            
        urls.append(url)
        console.print(f"[green]✓[/green] Added: {url}")
    
    # Remove duplicates while preserving order
    urls = deduplicate_urls(urls)
    
    # Final validation
    valid_urls, invalid_urls = validate_urls_batch(urls)
    
    if invalid_urls:
        console.print("\n[yellow]Warning: Some URLs failed validation:[/yellow]")
        for url, error in invalid_urls:
            console.print(f"  [red]✗[/red] {url}: {error}")
    
    return valid_urls


@click.command()
@click.option(
    "--config", 
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file (default: config.json)"
)
@click.option(
    "--create-config",
    is_flag=True,
    help="Create a sample configuration file and exit"
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default="api-doc-processor-data",
    help="Output directory for generated files"
)
@click.option(
    "--test-only",
    is_flag=True,
    help="Test API connections and exit"
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output"
)
def main(config_path: Path, create_config: bool, output_dir: Path, test_only: bool, verbose: bool):
    """API Documentation Processor - Automate API integration research."""
    
    if create_config:
        config_file = config_path or Path("config.json")
        create_sample_config(config_file)
        return
    
    print_header()
    
    # Load configuration
    try:
        config = load_config(config_path)
        console.print("[green]✓[/green] Configuration loaded successfully")
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("\n[yellow]Tip:[/yellow] Run with --create-config to generate a sample configuration")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        sys.exit(1)
    
    # Collect URLs
    urls = collect_urls()
    
    if not urls:
        console.print("[yellow]No URLs provided. Exiting.[/yellow]")
        return
    
    console.print(f"\n[bold]Processing {len(urls)} URL(s):[/bold]")
    for i, url in enumerate(urls, 1):
        console.print(f"  {i}. {url}")
    
    # Create output directory
    output_dir.mkdir(exist_ok=True)
    markdown_dir = output_dir / "markdown"
    specs_dir = output_dir / "specs"
    markdown_dir.mkdir(exist_ok=True)
    specs_dir.mkdir(exist_ok=True)
    
    console.print(f"\n[bold]Output directory:[/bold] {output_dir}")
    
    # Initialize clients
    try:
        firecrawl_client = FirecrawlClient(config.firecrawl)
        llm_client = LLMClient(config.litellm)
    except Exception as e:
        console.print(f"[red]Client initialization error:[/red] {str(e)}")
        sys.exit(1)
    
    # Test connections
    console.print("\n[bold]Testing API connections...[/bold]")
    
    try:
        firecrawl_ok, firecrawl_msg = firecrawl_client.test_connection()
        if firecrawl_ok:
            console.print(f"[green]✓[/green] {firecrawl_msg}")
        else:
            console.print(f"[red]✗[/red] {firecrawl_msg}")
            if verbose:
                console.print(f"[dim]Debug info: Check your Firecrawl API key in config.json[/dim]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Firecrawl connection failed: {str(e)}")
        sys.exit(1)
    
    try:
        llm_ok, llm_msg = llm_client.test_connection()
        if llm_ok:
            console.print(f"[green]✓[/green] {llm_msg}")
        else:
            console.print(f"[red]✗[/red] {llm_msg}")
            if verbose:
                console.print(f"[dim]Debug info: Check your LLM API key and model settings in config.json[/dim]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] LLM connection failed: {str(e)}")
        sys.exit(1)
    
    if test_only:
        console.print("\n[green]✓[/green] All API connections successful!")
        return
    
    # Process URLs
    console.print(f"\n[bold]Step 1: Crawling documentation...[/bold]")
    
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            crawl_task = progress.add_task("Crawling URLs...", total=len(urls))
            
            crawl_results = firecrawl_client.scrape_urls_batch(urls, markdown_dir)
            
            progress.update(crawl_task, advance=len(urls), description="Crawling complete!")
    except Exception as e:
        console.print(f"\n[red]Error during crawling:[/red] {str(e)}")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)
    
    # Show crawling results
    successful_crawls = []
    failed_crawls = []
    
    for url, result in crawl_results.items():
        if result["success"]:
            successful_crawls.append((url, result))
            console.print(f"[green]✓[/green] {url} → {Path(result['filepath']).name}")
        else:
            failed_crawls.append((url, result))
            console.print(f"[red]✗[/red] {url}: {result['error']}")
    
    if not successful_crawls:
        console.print("\n[red]Error:[/red] No URLs were successfully crawled")
        sys.exit(1)
    
    if failed_crawls:
        console.print(f"\n[yellow]Warning:[/yellow] {len(failed_crawls)} URL(s) failed to crawl")
    
    # Generate tech spec
    console.print(f"\n[bold]Step 2: Generating technical specification...[/bold]")
    
    markdown_files = [Path(result["filepath"]) for url, result in successful_crawls]
    
    # Show token estimation
    token_estimate = llm_client.estimate_token_usage(markdown_files)
    if "error" not in token_estimate:
        console.print(f"[dim]Estimated tokens: ~{token_estimate['estimated_input_tokens']} input + {token_estimate['max_output_tokens']} output[/dim]")
    
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            llm_task = progress.add_task("Generating tech spec...", total=None)
            
            spec_success, tech_spec, spec_error = llm_client.generate_tech_spec(
                markdown_files, config.prompt
            )
            
            if spec_success:
                spec_filepath = llm_client.save_tech_spec(tech_spec, specs_dir)
                progress.update(llm_task, description="Tech spec generated!")
            else:
                progress.update(llm_task, description="Tech spec generation failed!")
    except Exception as e:
        console.print(f"\n[red]Error during tech spec generation:[/red] {str(e)}")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)
    
    if spec_success:
        console.print(f"\n[green]✓[/green] Technical specification generated!")
        console.print(f"[dim]Saved to: {spec_filepath}[/dim]")
        
        # Display preview of tech spec
        console.print(f"\n[bold]Preview of generated specification:[/bold]")
        preview = tech_spec[:500] + "..." if len(tech_spec) > 500 else tech_spec
        console.print(Panel(preview, border_style="green", title="Tech Spec Preview"))
        
        console.print(f"\n[bold]Summary:[/bold]")
        console.print(f"• Processed {len(successful_crawls)} documentation source(s)")
        console.print(f"• Generated {len(tech_spec)} character specification")
        console.print(f"• Results saved to: {output_dir}")
    else:
        console.print(f"\n[red]Error generating tech spec:[/red] {spec_error}")
        sys.exit(1)


if __name__ == "__main__":
    main()