"""
IT Helpdesk Multi-Agent AI System
Main application entry point providing CLI and Web Server modes.
"""
import argparse
import asyncio
import os
import sys

def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Launches the FastAPI application via Uvicorn."""
    import uvicorn
    from it_helpdesk_agent.fast_api_app import app
    print(f"🚀 Starting IT Helpdesk Web Server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)

def run_cli():
    """Runs the local interactive test loop."""
    import test_local
    print("🤖 Starting IT Helpdesk Local Interactive Runner...")
    asyncio.run(test_local.main())

def main():
    parser = argparse.ArgumentParser(
        description="IT Helpdesk Multi-Agent AI System: 3-Tier Support, RAG, & RCA System."
    )
    parser.add_argument(
        "--mode",
        choices=["serve", "cli"],
        default=os.getenv("RUN_MODE", "serve"),
        help="Execution mode: 'serve' to run FastAPI app, 'cli' for local interactive testing."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host binding for web server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8080")),
        help="Port for web server (default: 8080)"
    )

    args = parser.parse_args()

    if args.mode == "serve":
        run_server(host=args.host, port=args.port)
    elif args.mode == "cli":
        run_cli()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
