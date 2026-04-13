{
  "permissions": {
    "allow": [
      "Bash(pip3 list:*)",
      "Bash(pip3 install:*)",
      "Bash(python3 -c \"import py_compile; py_compile.compile\\('malaysia_sourcer.py', doraise=True\\)\")",
      "Bash(npm install:*)",
      "Bash(npx next:*)",
      "Bash(python3 -c \"import py_compile; py_compile.compile\\('api/search.py', doraise=True\\); py_compile.compile\\('api/enrich.py', doraise=True\\); py_compile.compile\\('api/export.py', doraise=True\\); print\\('All Python files compile OK'\\)\")",
      "Bash(npx vercel:*)",
      "Bash(python3 -c \"import py_compile; py_compile.compile\\('api/describe.py', doraise=True\\); py_compile.compile\\('api/export.py', doraise=True\\); print\\('Python OK'\\)\")",
      "Bash(python3 -c \"import py_compile; py_compile.compile\\('api/describe.py', doraise=True\\); print\\('OK'\\)\")",
      "Bash(vercel --prod)",
      "Bash(vercel logs:*)",
      "Bash(vercel inspect:*)",
      "Bash(curl -s \"https://geo-deal-sourcing-my.vercel.app/api/saves\")",
      "Bash(curl -s -X POST \"https://geo-deal-sourcing-my.vercel.app/api/search\" -H \"Content-Type: application/json\" -d '{\"postcode\":\"43000\"}')",
      "Bash(vercel env:*)",
      "Bash(vercel --prod)"
    ]
  }
}
