# WISE Dashboard - Simple commands to run the dashboard

# Default: start the server
.PHONY: start server open help

# Start a local web server and open the dashboard
start: server

# Start Python's built-in HTTP server on port 8001
server:
	@echo "Starting local server at http://localhost:8001"
	@echo "Opening WISE_Dashboard.html..."
	@echo "Press Ctrl+C to stop the server"
	@echo ""
	@open "http://localhost:8001/WISE_Dashboard.html" 2>/dev/null || xdg-open "http://localhost:8001/WISE_Dashboard.html" 2>/dev/null &
	@python3 -m http.server 8001

# Just open the URL (assumes server is already running)
open:
	@open "http://localhost:8001/WISE_Dashboard.html" 2>/dev/null || xdg-open "http://localhost:8001/WISE_Dashboard.html" 2>/dev/null

# Show help
help:
	@echo "WISE Dashboard Commands:"
	@echo ""
	@echo "  make start   - Start server and open dashboard (default)"
	@echo "  make server  - Start server and open dashboard"
	@echo "  make open    - Open dashboard (if server already running)"
	@echo "  make help    - Show this help message"
	@echo ""
	@echo "To add new data:"
	@echo "  1. Edit WISE_Quarterly_Metrics.csv"
	@echo "  2. Add a new row with the quarterly metrics"
	@echo "  3. Refresh the browser"
