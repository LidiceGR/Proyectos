## procesar sistema 1

 & "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python source_extractor\extractor.py --config config\mock_req_dummy_sistema1_sources.json --destination workspace\run_001 

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m sql_ast_parsers --manifest workspace\run_001\sources_manifest.json --destination workspace\run_001\ast_files --max-files 1


## procesar sistema 2 
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python source_extractor\extractor.py --config config\mock_req_dummy_system2.json --destination workspace\run_002      


& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m sql_ast_parsers --manifest workspace\run_002\sources_manifest.json --destination workspace\run_002\ast_files


## corre rel inventario 

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m inventory_builder --ast-manifest workspace\run_001\ast_files\ast_manifest.json --source-manifest workspace\run_001\sources_manifest.json --destination workspace\run_001\metadata