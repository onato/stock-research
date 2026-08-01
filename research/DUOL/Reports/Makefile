# DUOL Dashboard - Simple commands to run the dashboard

# Default: start the server
.PHONY: start server open help

# Start a local web server and open the dashboard
start: server

# Start Python's built-in HTTP server on port 8000
server:
	@echo "Starting local server at http://localhost:8000"
	@echo "Opening DUOL_Dashboard.html..."
	@echo "Press Ctrl+C to stop the server"
	@echo ""
	@open "http://localhost:8000/DUOL_Dashboard.html" 2>/dev/null || xdg-open "http://localhost:8000/DUOL_Dashboard.html" 2>/dev/null &
	@python3 -m http.server 8000

# Just open the URL (assumes server is already running)
open:
	@open "http://localhost:8000/DUOL_Dashboard.html" 2>/dev/null || xdg-open "http://localhost:8000/DUOL_Dashboard.html" 2>/dev/null

# Show help
help:
	@echo "DUOL Dashboard Commands:"
	@echo ""
	@echo "  make start   - Start server and open dashboard (default)"
	@echo "  make server  - Start server and open dashboard"
	@echo "  make open    - Open dashboard (if server already running)"
	@echo "  make help    - Show this help message"
	@echo ""
	@echo "To add new data:"
	@echo "  1. Edit DUOL_Metrics_Analysis.csv"
	@echo "  2. Add a new row with format:"
	@echo "     Period,Revenue,Bookings,DAU,MAU,Subscribers,SubBookings,EBITDA,EBITDAMargin,NetIncome,FCF"
	@echo "  3. Refresh the browser"
