#!/bin/bash
# Example script demonstrating readwebform usage

echo "=== Example 1: Simple declarative form ==="
echo "This will create a simple feedback form"
echo ""

# Uncomment to run:
# readwebform \
#   --title "Feedback Form" \
#   --text "Please provide your feedback" \
#   --field name:text:Full+Name:required \
#   --field email:email:Email:required \
#   --field rating:number:Rating:min=1,max=5,required \
#   --field comments:textarea:Comments:rows=5 \
#   --launch-browser

echo "=== Example 2: Using an HTML file ==="
echo "This will serve the contact form from contact.html"
echo ""

# Uncomment to run:
# result=$(readwebform --htmlfile examples/contact.html --launch-browser)
# echo "Result:"
# echo "$result" | jq .

echo "=== Example 3: File upload with size limits ==="
echo "This will create a file upload form with size restrictions"
echo ""

# Uncomment to run:
# readwebform \
#   --htmlfile examples/file-upload.html \
#   --max-file-size 5M \
#   --max-total-size 10M \
#   --timeout 600 \
#   --launch-browser

echo "=== Example 4: Output to environment file ==="
echo "This will save form data as environment variables"
echo ""

# Uncomment to run:
# readwebform \
#   --field username:text:Username:required \
#   --field password:password:Password:required \
#   --envfile vars.env \
#   --launch-browser
#
# source vars.env
# echo "Username: $WEBFORM_USERNAME"

echo "=== Example 5: Inline HTML ==="
echo "This demonstrates using inline HTML"
echo ""

# Uncomment to run:
# readwebform \
#   --html '<form><input name="quick_input" placeholder="Enter something" required><button>Submit</button></form>' \
#   --title "Quick Input" \
#   --launch-browser

echo ""
echo "Uncomment the examples above to try them!"
