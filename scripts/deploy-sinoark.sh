#!/bin/bash
# Quick redeploy sinoark-web to Vercel
set -e

cd /root/sinoark-web

echo "🚀 Deploying sinoark-web to Vercel..."
echo ""

# Commit changes if any
if [[ -n $(git status -s) ]]; then
    echo "📝 Committing changes..."
    git add .
    git commit -m "auto: $(date -u '+%Y-%m-%d %H:%M UTC')"

    # Push to GitHub if remote exists
    if git remote | grep -q origin; then
        echo "⬆️  Pushing to GitHub..."
        git push origin master 2>/dev/null || echo "⚠️  GitHub push skipped"
    fi
fi

# Deploy to Vercel
echo "🚀 Deploying to production..."
vercel --prod --yes \
  --token="$VERCEL_TOKEN" \
  --scope sinoarkmedia-3040s-projects

echo ""
echo "✅ Deployed successfully!"
echo ""
echo "🌐 Your site: https://sinoark-web.vercel.app"
echo ""
