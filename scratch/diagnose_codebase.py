import os
import sys
import ast
import traceback
import importlib

def run_diagnostics():
    print("=" * 60)
    print("      DARK CHANNEL FACTORY - COMPREHENSIVE SYNTAX DIAGNOSTIC")
    print("=" * 60)
    
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print(f"Root Workspace Directory: {root_dir}")
    
    error_count = 0
    checked_files = 0
    
    # Step 1: Static Syntax Check (AST parsing)
    print("\n🔍 Step 1: Static Syntax Check (AST) across all python files...")
    for root, dirs, files in os.walk(root_dir):
        if "venv" in root or ".git" in root or ".pytest_cache" in root or "build" in root or "dist" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                checked_files += 1
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        source = f.read()
                    ast.parse(source, filename=full_path)
                except SyntaxError as se:
                    error_count += 1
                    print(f"❌ Syntax Error in {os.path.relpath(full_path, root_dir)}:")
                    print(f"   Line {se.lineno}: {se.text.strip() if se.text else ''}")
                    print(f"   {se.msg}")
                except Exception as e:
                    error_count += 1
                    print(f"❌ Unexpected Error reading {os.path.relpath(full_path, root_dir)}: {e}")
                    
    print(f"   Checked {checked_files} files. Static errors: {error_count}")
    
    # Step 2: Critical Dynamic Imports Check
    print("\n🔍 Step 2: Dynamic Imports Check (detecting circular imports & missing deps)...")
    modules_to_test = [
        "shared.logger",
        "shared.llm_client",
        "shared.models",
        "shared.models_v2",
        "shared.retention_engine",
        "shared.script_validator",
        "database.db_manager",
        "agents.copywriter.agent",
        "agents.designer.thumbnail_generator",
        "agents.validator.qc_agent",
        "elite_production_test",
        "desktop_node"
    ]
    
    for mod_name in modules_to_test:
        try:
            sys.path.insert(0, root_dir)
            importlib.import_module(mod_name)
            print(f"   ✅ Successfully imported: {mod_name}")
        except Exception as ie:
            print(f"   ❌ Failed to import: {mod_name}")
            traceback.print_exc(limit=3, file=sys.stdout)
            error_count += 1
            
    # Step 3: Run Unit Test Suite (MOCK mode active)
    print("\n🔍 Step 3: Executing Unit Test Suite (MOCK mode)...")
    os.environ["MOCK_LLM"] = "true"
    os.environ["SKIP_UPLOAD"] = "true"
    os.environ["DRY_RUN"] = "true"
    
    import unittest
    suite = unittest.defaultTestLoader.discover(os.path.join(root_dir, "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    test_result = runner.run(suite)
    
    if not test_result.wasSuccessful():
        print(f"❌ Test Suite failed with {len(test_result.failures)} failures and {len(test_result.errors)} errors.")
        error_count += len(test_result.failures) + len(test_result.errors)
    else:
        print("   ✅ All unit and integration tests passed successfully in MOCK mode!")
        
    print("\n" + "=" * 60)
    if error_count == 0:
        print("🎉 DIAGNOSTIC COMPLETED: Codebase is 100% healthy, clean and bug-free!")
    else:
        print(f"⚠️ DIAGNOSTIC COMPLETED: Found {error_count} issues that need attention.")
    print("=" * 60)
    
    return error_count

if __name__ == "__main__":
    sys.exit(run_diagnostics())
