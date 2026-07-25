#!/usr/bin/env python3
import os
import requests
import json
import re
import webbrowser
from html import unescape
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_red_hat_access_token(offline_token):
    """Exchanges an offline token for a 15-minute access token."""
    token_url = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
    data = {
        'grant_type': 'refresh_token',
        'client_id': 'rhsm-api',
        'refresh_token': offline_token
    }
    response = requests.post(token_url, data=data)
    if response.status_code == 200:
        return response.json().get('access_token')
    else:
        raise Exception(f"Authentication failed: {response.status_code} - {response.text}")

def search_v2_kcs(access_token, query_string, num_results=10):
    """Performs a KCS search using the V2 POST endpoint.
    
    Args:
        access_token: Red Hat API access token
        query_string: Search query keywords
        num_results: Number of results to return (default: 10, max: 100)
    """
    # Base URL for the KCS V2 search service
    search_url = "https://api.access.redhat.com/support/search/v2/kcs"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Limit max results to 100 to avoid overwhelming output
    num_results = min(num_results, 100)
    
    # Body parameters as specified in your screenshot
    payload = {
        "q": query_string,         # Your search keywords
        "expression": "",          # Advanced Solr expression (optional)
        "start": 0,                # Starting index for pagination
        "rows": num_results,       # Number of results per page
        "clientName": "python_cli" # Custom identifier for your request
    }
    
    response = requests.post(search_url, headers=headers, json=payload)
    
    if response.status_code == 200:
        return response.json()
    else:
        return f"Search failed: {response.status_code} - {response.text}"

def strip_html(text):
    """Remove HTML tags and clean up text."""
    if not text:
        return text
    
    # Handle lists by joining them
    if isinstance(text, list):
        text = ' '.join(str(item) for item in text if item)
    
    # Convert to string if not already
    if not isinstance(text, str):
        text = str(text)
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = unescape(text)
    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def display_kcs_results(results, debug_mode=False, open_browser=False):
    """Display KCS search results in a user-friendly format for troubleshooting.
    
    Args:
        results: Search results from the KCS API
        debug_mode: Show all available fields and raw document data
        open_browser: Automatically open URLs in web browser
    """
    if isinstance(results, str):
        print(f"Error: {results}")
        return
    
    # Extract response metadata
    response = results.get('response', {})
    num_found = response.get('numFound', 0)
    start = response.get('start', 0)
    docs = response.get('docs', [])
    
    print("\n" + "="*100)
    print(f"SEARCH RESULTS: Found {num_found} articles (showing {len(docs)} results starting from {start})")
    print("="*100)
    
    if not docs:
        print("\nNo results found. Try different search terms.")
        return
    
    # Collect URLs to open
    urls_to_open = []
    
    for idx, doc in enumerate(docs, 1):
        print(f"\n[{idx}] {'-'*95}")
        
        # Debug mode: show all available fields
        if debug_mode:
            print("\n🔍 DEBUG - Available fields in this document:")
            print(json.dumps(list(doc.keys()), indent=2))
            print("\n🔍 DEBUG - Full document content:")
            print(json.dumps(doc, indent=2))
            print("-" * 95)
        
        # Article Title
        title = doc.get('allTitle', doc.get('documentTitle', 'No Title'))
        print(f"Title: {title}")
        
        # Article ID and View URL
        doc_id = doc.get('id', 'N/A')
        view_url = doc.get('view_uri', f"https://access.redhat.com/solutions/{doc_id}")
        print(f"ID: {doc_id}")
        print(f"🔗 URL: {view_url}")
        
        # Store URL for browser opening
        urls_to_open.append(view_url)
        
        # Document Type
        doc_type = doc.get('documentKind', 'N/A')
        print(f"Type: {doc_type}")
        
        # Abstract/Summary
        abstract = doc.get('abstract', doc.get('allDescription', ''))
        abstract = strip_html(abstract) if abstract else 'No summary available'
        if abstract and len(abstract) > 300:
            abstract = abstract[:300] + "..."
        print(f"Summary: {abstract}")
        
        # Last Modified Date
        modified_date = doc.get('lastModifiedDate', 'N/A')
        print(f"Last Modified: {modified_date}")
        
        # Products (if available)
        products = doc.get('product', [])
        if products:
            if isinstance(products, list):
                print(f"Products: {', '.join(products[:3])}")  # Show first 3 products
            else:
                print(f"Products: {products}")
        
        # Resolution confidence/severity (if available)
        severity = doc.get('severity', '')
        if severity:
            print(f"Severity: {severity}")
        
        # Case count (popularity indicator)
        case_count = doc.get('caseCount', 0)
        if case_count:
            print(f"Cases Referenced: {case_count}")
        
        # Environment - try multiple possible field names
        # Note: solution_environment might be a list
        environment = (doc.get('solution_environment', '') or
                      doc.get('environment', '') or
                      doc.get('allEnvironment', ''))
        
        if environment:
            # Handle list type
            if isinstance(environment, list):
                environment = '\n   - '.join(str(item) for item in environment if item)
                environment = '- ' + environment  # Add bullet to first item
            
            environment = strip_html(environment)
            print(f"\n🖥️  ENVIRONMENT:")
            # Truncate if too long
            if len(environment) > 800:
                environment = environment[:800] + "... (see full article for complete details)"
            print(f"   {environment}")
        
        # Issue/Problem Description - try multiple possible field names
        # Note: solution_issue might be a list
        issue = (doc.get('solution_issue', '') or
                doc.get('issue', '') or
                doc.get('allIssue', ''))
        
        if issue:
            # Handle list type
            if isinstance(issue, list):
                issue = '\n   '.join(str(item) for item in issue if item)
            
            issue = strip_html(issue)
            print(f"\n💡 ISSUE:")
            # Truncate if too long
            if len(issue) > 800:
                issue = issue[:800] + "... (see full article for complete details)"
            print(f"   {issue}")
        
        # Resolution/Solution - try multiple possible field names
        # Note: solution_resolution is typically a list
        resolution = (doc.get('solution_resolution', '') or
                     doc.get('resolution', '') or 
                     doc.get('solution', '') or 
                     doc.get('allBody', '') or
                     doc.get('body', '') or
                     doc.get('allContent', ''))
        
        if resolution:
            # Handle list type (solution_resolution is typically a list)
            if isinstance(resolution, list):
                resolution = '\n'.join(str(item) for item in resolution if item)
            
            resolution = strip_html(resolution)
            
            # Replace markdown code blocks (~~~) with better formatting
            resolution = resolution.replace('~~~', '\n')
            
            print(f"\n✅ RESOLUTION/SOLUTION:")
            # Truncate if too long
            if len(resolution) > 2000:
                resolution = resolution[:2000] + "... (see full article for complete details)"
            print(f"   {resolution}")
        
        # If no resolution found, indicate it's in the full article
        if not resolution and not debug_mode:
            print(f"\n✅ RESOLUTION/SOLUTION:")
            print(f"   [Full solution available at: {view_url}]")
    
    print("\n" + "="*100)
    print(f"Total Results: {num_found} | Showing: {len(docs)}")
    print("="*100 + "\n")
    
    # Open URLs in browser if requested
    if open_browser and urls_to_open:
        print(f"🌐 Opening {len(urls_to_open)} article(s) in web browser...")
        for url in urls_to_open:
            try:
                webbrowser.open(url)
                print(f"   ✅ Opened: {url}")
            except Exception as e:
                print(f"   ❌ Failed to open {url}: {e}")
        print()

if __name__ == "__main__":
    # Parameters provided by user
    #user_offline_token = input("Enter your Red Hat offline token: ")
    search_query = input("Enter search string (e.g. 'error crash failed'): ")
    
    # Ask how many results to display
    num_results_input = input("Number of results to display (default 10, max 100): ").strip()
    if num_results_input:
        try:
            num_results = int(num_results_input)
            if num_results < 1:
                print("Invalid number, using default: 10")
                num_results = 10
        except ValueError:
            print("Invalid input, using default: 10")
            num_results = 10
    else:
        num_results = 10
    
    # Ask if debug mode should be enabled
    debug_input = input("Enable debug mode to see all available fields? (y/N): ").strip().lower()
    debug_mode = debug_input in ['y', 'yes']
    
    # Ask if raw JSON should be printed
    raw_json_input = input("Print raw JSON response for debugging? (y/N): ").strip().lower()
    print_raw_json = raw_json_input in ['y', 'yes']
    
    # Ask if URLs should be opened in browser
    browser_input = input("Open article URLs in web browser? (Y/n): ").strip().lower()
    open_browser = browser_input not in ['n', 'no']  # Default to yes

    try:
        print("Authenticating...")
        
        # Get offline token from environment variable
        user_offline_token = os.environ.get('REDHAT_OFFLINE_TOKEN')
        
        if not user_offline_token:
            print("Error: REDHAT_OFFLINE_TOKEN environment variable not set.")
            print("Please set it in your .env file or export it:")
            print("  export REDHAT_OFFLINE_TOKEN='your_token_here'")
            print("\nOr add to .env file:")
            print("  REDHAT_OFFLINE_TOKEN=your_token_here")
            exit(1)
        
        token = get_red_hat_access_token(user_offline_token)
        print(f"Executing V2 search for '{search_query}' (requesting {num_results} results)...")
        results = search_v2_kcs(token, search_query, num_results)
        
        # Print raw JSON if requested
        if print_raw_json:
            print("\n" + "="*100)
            print("RAW JSON RESPONSE (for debugging)")
            print("="*100)
            print(json.dumps(results, indent=2))
            print("="*100 + "\n")
        
        # Display results in a user-friendly format
        display_kcs_results(results, debug_mode=debug_mode, open_browser=open_browser)
        
    except Exception as e:
        print(f"An error occurred: {e}")
