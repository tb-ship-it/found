#!/usr/bin/env python3
"""
Flask Web Server for Pitch Deck Evaluator
Runs locally, no CORS issues, API key baked in
"""

from flask import Flask, request, jsonify, send_from_directory
import json
import os
from pathlib import Path
from anthropic import Anthropic
import pdfplumber
import tempfile

app = Flask(__name__)

# Your API key baked in
import os
API_KEY = os.environ.get('ANTHROPIC_API_KEY')

INVESTMENT_THESIS = """
You are evaluating startup pitch decks for Found Capital angel investment syndicate. 
This is an internal evaluation tool. Be brutally honest in your assessment.

EVALUATION CRITERIA (100 points total):

1. FOUNDERS (40 points)
   Sub-criteria:
   a) Commercial Energy, Authenticity, Commitment (12 points)
      - Do they radiate hunger and drive to win?
      - Are they genuine or performing?
      - Evidence of deep commitment (skin in game, full-time, personal sacrifice)
   
   b) Industry Experience (12 points)
      - Deep domain knowledge in this specific problem space
      - Relevant operational experience (not just adjacent)
      - Network effects from past roles (customers, partners, hires)
   
   c) Capacity for Flawless Execution (12 points)
      - Track record of shipping and scaling
      - Evidence of operational excellence (metrics, systems, process)
      - Ability to prioritise ruthlessly
   
   d) Previous Exit (4 points)
      - Founder has prior exit experience (bonus points, not mandatory)
      - Understanding of value creation and capture
   
   RED FLAGS: Solo founder with no co-founder pipeline, corporate careerists with no startup DNA, 
   unclear on why they're building this specifically, vague on execution milestones

2. FINANCE (30 points)
   Sub-criteria:
   a) Business Metrics: Resilient Growth & Defensible Margins (20 points)
      - Revenue trajectory showing compounding growth (not linear)
      - Unit economics that demonstrate margin expansion path
      - Evidence of retention/stickiness (NRR, churn, cohort behaviour)
      - Capital efficiency (how much growth per £ deployed)
   
   b) Round Metrics: Valuation & Use of Funds (10 points)
      - Valuation justified by traction and market opportunity
      - Clear, credible use of funds tied to specific milestones
      - Runway sufficient to hit next inflection point (18-24 months)
      - Deal structure reasonable (no toxic terms, fair dilution)
   
   RED FLAGS: Revenue deceleration, unsustainable burn, inflated valuation vs. comparables,
   vague use of funds ("growth" without specifics), toxic preference stack

3. FIT (30 points)
   Sub-criteria:
   a) Scalable Opportunity: Global Urgency (8 points)
      - Does the world need this problem solved NOW? (not "nice to have")
      - International expansion potential (not UK-only)
      - Secular tailwinds (regulatory, technological, behavioural shifts)
   
   b) Market Fit: Problem Understanding (8 points)
      - Do they understand this problem better than current solutions?
      - Evidence of proprietary insight (not obvious to everyone)
      - Customer validation (letters of intent, design partnerships, early revenue)
   
   c) Mission: Game Changer vs. Incremental (7 points)
      - Is this a 10x improvement or 10% better?
      - Potential to redefine category or create new one
      - Vision ambitious enough to attract exceptional talent
   
   d) Moatability (7 points)
      - Network effects, data moats, or switching costs
      - Barriers to replication by incumbents or well-funded copycats
      - First-mover advantages that compound over time
   
   RED FLAGS: "Nice to have" problem, crowded market with no differentiation, 
   unclear why this team wins vs. incumbents, easily replicated by Big Tech

DISQUALIFIERS (automatic fail):
- Founders not full-time or hedging with other projects
- Obvious integrity issues (exaggerated claims, contradictory data)
- Legal/IP landmines (unclear ownership, patent trolls, regulatory blockers)
- No credible path to £10M+ ARR within 5 years
- Misalignment on ambition (lifestyle business, not scale-up)

SCORING GUIDE:
90-100: Top 1% deal, angel syndicate should fight to get allocation
75-89: Strong opportunity, worth leading or co-leading round
60-74: Maybe, depends on price and co-investor quality
<60: Pass cleanly

OUTPUT FORMAT:
Return ONLY valid JSON (no markdown, no commentary):
{
  "overall_score": <number 0-100>,
  "verdict": "<STRONG BACK | BACK | MAYBE | WEAK | PASS>",
  "category_scores": {
    "founders": <0-40>,
    "finance": <0-30>,
    "fit": <0-30>
  },
  "founders_breakdown": {
    "commercial_energy": <0-12>,
    "industry_experience": <0-12>,
    "execution_capacity": <0-12>,
    "previous_exit": <0-4>
  },
  "finance_breakdown": {
    "business_metrics": <0-20>,
    "round_metrics": <0-10>
  },
  "fit_breakdown": {
    "scalable_opportunity": <0-8>,
    "market_fit": <0-8>,
    "mission_ambition": <0-7>,
    "moatability": <0-7>
  },
  "strengths": ["<concise strength 1>", "<strength 2>", ...],
  "concerns": ["<red flag 1>", "<concern 2>", ...],
  "deal_breakers": ["<critical issue 1>", ...],
  "key_questions": ["<question for founder 1>", "<question 2>", ...],
  "comparable_companies": ["<similar co 1>", "<similar co 2>", ...],
  "investment_thesis": "<2-3 sentence case for backing or passing>"
}

Be ruthlessly honest. This is internal decision-making, not founder feedback.
"""


def extract_text_from_pdf(pdf_path):
    """Extract text content from PDF"""
    text_content = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            page_text = page.extract_text()
            if page_text:
                text_content.append(f"--- SLIDE {i} ---\n{page_text}\n")
    
    return "\n".join(text_content)


def evaluate_deck(deck_content):
    """Evaluate deck content using Claude API"""
    client = Anthropic(api_key=API_KEY)
    
    prompt = f"""{INVESTMENT_THESIS}

PITCH DECK CONTENT:
{deck_content}

Analyze this pitch deck thoroughly against the investment thesis above.
Return ONLY valid JSON with no additional commentary."""
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        temperature=0.3,
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    
    result_text = response.content[0].text
    
    # Handle markdown code blocks if present
    if "```json" in result_text:
        result_text = result_text.split("```json")[1].split("```")[0]
    elif "```" in result_text:
        result_text = result_text.split("```")[1].split("```")[0]
    
    return json.loads(result_text.strip())


@app.route('/evaluate-batch', methods=['POST'])
def evaluate_batch():
    """Batch evaluate multiple decks and filter by score threshold"""
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No files uploaded'}), 400
        
        files = request.files.getlist('files')
        threshold = int(request.form.get('threshold', 60))
        
        results = []
        
        for file in files:
            if not file.filename.endswith('.pdf'):
                continue
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name
            
            try:
                # Extract and evaluate
                deck_content = extract_text_from_pdf(tmp_path)
                
                if not deck_content or len(deck_content.strip()) < 100:
                    results.append({
                        'filename': file.filename,
                        'error': 'Could not extract text from PDF',
                        'dismissed': True
                    })
                    continue
                
                evaluation = evaluate_deck(deck_content)
                
                # Check threshold
                score = evaluation.get('overall_score', 0)
                dismissed = score < threshold
                
                results.append({
                    'filename': file.filename,
                    'evaluation': evaluation,
                    'dismissed': dismissed,
                    'reason': f'Score {score} below threshold {threshold}' if dismissed else None
                })
                
            finally:
                os.unlink(tmp_path)
        
        return jsonify({
            'total': len(files),
            'evaluated': len([r for r in results if 'evaluation' in r]),
            'passed': len([r for r in results if not r.get('dismissed', True)]),
            'dismissed': len([r for r in results if r.get('dismissed', True)]),
            'results': results
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'web-interface.html')


@app.route('/evaluate', methods=['POST'])
def evaluate():
    """API endpoint to evaluate a pitch deck"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.pdf'):
            return jsonify({'error': 'Only PDF files are supported'}), 400
        
        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name
        
        try:
            # Extract text
            deck_content = extract_text_from_pdf(tmp_path)
            
            if not deck_content or len(deck_content.strip()) < 100:
                return jsonify({
                    'error': 'Could not extract sufficient text from PDF. File may be image-based.'
                }), 400
            
            # Evaluate
            evaluation = evaluate_deck(deck_content)
            
            return jsonify(evaluation)
            
        finally:
            # Clean up temp file
            os.unlink(tmp_path)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("\n" + "="*70)
    print("🚀 FOUND CAPITAL DECK EVALUATOR")
    print("="*70)
    print("\n📍 Server running at: http://localhost:5000")
    print("\n📝 Instructions:")
    print("   1. Open your browser to http://localhost:5000")
    print("   2. Drag and drop a pitch deck PDF")
    print("   3. Get your evaluation in 20-30 seconds")
    print("\n⚠️  Press CTRL+C to stop the server")
    print("="*70 + "\n")
    
    app.run(debug=True, port=5000)
