## procesar sistema 1

 & "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python source_extractor\extractor.py --config config\mock_req_dummy_sistema1_sources.json --destination workspace\run_001 

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m sql_ast_parsers --manifest workspace\run_001\sources_manifest.json --destination workspace\run_001\ast_files --max-files 1


## procesar sistema 2 
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python source_extractor\extractor.py --config config\mock_req_dummy_system2.json --destination workspace\run_002      


& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m sql_ast_parsers --manifest workspace\run_002\sources_manifest.json --destination workspace\run_002\ast_files


## corre rel inventario 

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m inventory_builder --ast-manifest workspace\run_001\ast_files\ast_manifest.json --source-manifest workspace\run_001\sources_manifest.json --destination workspace\run_001\metadata


## correr el research_agent solo para ver entradas 

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_001\research\research_tasks.json --task-id TASK-227F0C005747 --preview


## correr el research_agent para ver ya un promt al LLM

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_001\research\research_tasks.json --task-id TASK-227F0C005747

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_001\research\research_tasks.json --task-id TASK-3F9385154FA5

## ejecutar el primer programa  donde se llamaran los inicializadores y luego el primer llamdo al LLM 

### solo informativo 
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.example.json --dry-run

### solo hasta inventarios y tareas de investigación 

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.example.json

### Diferentes ejmeplos segun lo quehay en el archivo de confihruiación 
& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.example.json

### correr batch

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m capstone_app --config config\capstone_pipeline.batch.example.json --dry-run

## Nuevo particionadro de tareas  le manda un worflow para analizar un SP completo  y no solo una instrucción SQl

& "D:\devenv\Anaconda32024.02\Scripts\conda.exe" run --no-capture-output -n cmu-llm python -m research_agent --tasks workspace\run_007\research_v02\research_tasks.json --task-id TASK-8630EC5C6C6A