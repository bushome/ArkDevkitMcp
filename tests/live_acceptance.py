"""Live regression suite. Requires an open DevKit and the ArkMCPTesting mod.

Creates its own unique Blueprint, verifies persisted state and wiring, then
deletes only that fixture. Never lays out or saves existing user assets.
Run: py -3.11 tests/live_acceptance.py <report-directory>
"""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid


def main():
    directory = Path(sys.argv[1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    fixture = '/ArkMCPTesting/_MCPAcceptance/BP_' + uuid.uuid4().hex[:12]
    process = subprocess.Popen([sys.executable, '-u', str(root/'tests/live_stdio_client.py'), str(directory/'mcp.json')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', cwd=root)
    output = queue.Queue()
    def reader():
        for line in process.stdout:
            output.put(json.loads(line))
        output.put(None)
    threading.Thread(target=reader, daemon=True).start()
    results = []
    created = False
    failure = None

    def request(command):
        process.stdin.write(json.dumps(command)+'\n')
        process.stdin.flush()
        answer = output.get(timeout=180)
        if answer is None:
            raise RuntimeError('Live client exited: '+process.stderr.read())
        return answer

    def call(tool, args=None, error=False):
        answer = request(dict(tool=tool, args=args or {}))
        if bool(answer.get('isError')) != error:
            raise AssertionError(f'{tool}: {answer}')
        return answer['data']

    def graph():
        return {n['id']: n for n in call('list_graph',dict(path=fixture))['nodes']}

    def check(name, condition):
        results.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)

    def add(kind, **kwargs):
        return call('add_node',dict(path=fixture,kind=kind,**kwargs))['node']

    try:
        init = output.get(timeout=30)
        check('Real MCP initialization', init['data']['protocolVersion']=='2025-06-18')
        advertised = request(dict(method='tools/list'))['data']['tools']
        check('New tools advertised', {'create_blueprint','add_reroute','route_bus','refit_comment'} <= {t['name'] for t in advertised})
        check('Editor connected', call('editor_status')['connected_to']=='ShooterGame')
        reference = call('blueprint_info',dict(path='/ArkMCPTesting/BP_McpTest1'))
        call('create_blueprint',dict(path=fixture)); created=True
        original = graph()
        comment = add('comment',text='Acceptance comment',x=-100,y=-100,width=500,height=800)
        check('New comment has GUID', comment['guid_valid'] and bool(comment['guid']))
        before = graph()
        fitted = call('refit_comment',dict(path=fixture,node=comment['id'],nodes=list(original)[:2],padding=40))['comment']
        check('Refit preserves comment identity', fitted['guid']==comment['guid'])
        check('Refit moves no graph nodes', all(graph()[n]==original[n] for n in original))
        check('Comment listed', graph()[comment['id']]['class']=='EdGraphNode_Comment')
        saved=call('save_assets',dict(paths=[fixture]))
        check('Save succeeds', saved['failed']==[] and saved['saved']==[fixture])
        code=f"import unreal,json\ndef _mcp_reload(path):\n    result=unreal.EditorLoadingAndSavingUtils.reload_packages([unreal.load_asset(path).get_outermost()],unreal.ReloadPackagesInteractionMode.ASSUME_NEGATIVE)\n    print(json.dumps({{'reloaded':result[0]}}))\n_mcp_reload({fixture!r})\ndel _mcp_reload"
        check('Package actually reloaded',call('run_python',dict(code=code))['reloaded'])
        loaded=graph()[comment['id']]
        check('GUID preserved through disk reload', loaded['guid_valid'] and loaded['guid']==comment['guid'])
        check('Bounds preserved through disk reload', loaded['pos']==fitted['pos'] and loaded['size']==fitted['size'])
        call('create_blueprint',dict(path=fixture),error=True)
        check('Existing asset unchanged after creation refusal', graph()[comment['id']]==loaded)
        call('add_variable',dict(path=fixture,name='BusCount',type='int',default='5'))
        call('compile_blueprint',dict(path=fixture))
        getter=add('get_variable',name='BusCount',x=0,y=1200)
        first=add('action',name='Utilities|Operators|Greater(>)',x=600,y=1200)
        second=add('action',name='Utilities|Operators|Greater(>)',x=1200,y=1200)
        before=graph()
        bus=dict(path=fixture,from_node=getter['id'],from_pin='BusCount',y=1600,
                 taps=[dict(node=first['id'],pin='A',x=300),dict(node=second['id'],pin='A',x=900)])
        routed=call('route_bus',bus)
        after=graph()
        check('Two reroute taps created',len(routed['reroutes'])==2)
        check('Existing positions preserved by bus',all(after[n]['pos']==before[n]['pos'] for n in before))
        check('Consumers receive integer data',all(next(p for p in after[n['id']]['pins'] if p['name']=='A')['type']=='Integer' for n in (first,second)))
        check('Bus has expected segment positions',[n['pos'] for n in routed['reroutes']]==[[300,1600],[900,1600]])
        call('route_bus',bus,error=True)
        check('Connected-input refusal preserves full graph',graph()==after)
        event=next(n for n in original.values() if n['class']=='K2Node_Event')
        call('route_bus',dict(bus,from_node=event['id'],from_pin='then'),error=True)
        check('Execution bus refused without changes',graph()==after)
        overlap=dict(bus,y=1200,taps=[dict(node=first['id'],pin='B',x=600)])
        call('route_bus',overlap,error=True)
        check('Overlapping tap refused without changes',graph()==after)
        branch=add('branch',x=1800,y=1200)
        before=graph()
        call('route_bus',dict(bus,taps=[dict(node=branch['id'],pin='Condition',x=1500)]),error=True)
        check('Implicit conversion refused without changes',graph()==before)
        # Exercise rollback against real editor objects, with a controlled failure
        # on the third connection. Restore the helper in the same editor command.
        failure_bus=dict(bus,y=2000,taps=[dict(node=first['id'],pin='B',x=300),dict(node=second['id'],pin='B',x=900)])
        code=f"import arkmcp_editor as a,json\ndef _mcp_rollback_probe():\n    original=a._pin_fn\n    attempts=0\n    def fail_third(name,*args):\n        nonlocal attempts\n        if name=='try_create_connection':\n            attempts+=1\n            if attempts==3:return False\n        return original(name,*args)\n    a._pin_fn=fail_third\n    failed=False\n    try:\n        a.t_route_bus({failure_bus!r})\n    except a.ToolError:\n        failed=True\n    finally:\n        a._pin_fn=original\n    print(json.dumps({{'failed_as_injected':failed,'attempts':attempts}}))\n_mcp_rollback_probe()\ndel _mcp_rollback_probe"
        recovery=call('run_python',dict(code=code))
        check('Rollback failure branch exercised',recovery==dict(failed_as_injected=True,attempts=3))
        check('Rollback removes new nodes and wires',graph()==before)
        extra=call('add_reroute',dict(path=fixture,x=2200,y=2000))['node']
        extra_comment=add('comment',text='Remove me',x=2100,y=1900,width=300,height=300)
        call('remove_nodes',dict(path=fixture,nodes=[extra['id'],extra_comment['id']]))
        check('Mixed K2/comment removal succeeds',extra['id'] not in graph() and extra_comment['id'] not in graph())
        call('tidy_graph',dict(path=fixture),error=True)
        call('refit_comment',dict(path=fixture,node=comment['id'],nodes=[]),error=True)
        call('refit_comment',dict(path=fixture,node=getter['id'],nodes=[branch['id']]),error=True)
        check('Rejected layout/comment requests preserve graph',graph()==before)
        # Cover every advertised add_node kind, including APIs whose arguments
        # differ from the original imported implementation notes.
        call('create_function',dict(path=fixture,name='AcceptanceFunction'))
        prepare=f"import unreal,json,arkmcp_editor as a\ndef _mcp_prepare_variants():\n    bp=unreal.load_asset({fixture!r})\n    ge=a._graph_editor(bp,'AcceptanceFunction')\n    assert ge.add_local_variable('LocalCount',a._pin_type('int'),'3')\n    assert unreal.BlueprintEditorLibrary.add_event_dispatcher(bp,'OnAcceptance')\n    ss=unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)\n    handles=ss.k2_gather_subobject_data_for_blueprint(bp)\n    handle,reason=ss.add_new_subobject(unreal.AddNewSubobjectParams(parent_handle=handles[0],new_class=unreal.StaticMeshComponent,blueprint_context=bp))\n    assert ss.rename_subobject(handle,unreal.Text('AcceptanceMesh')),str(reason)\n    print(json.dumps({{'prepared':True}}))\n_mcp_prepare_variants()\ndel _mcp_prepare_variants"
        check('Variant fixture prepared',call('run_python',dict(code=prepare))['prepared'])
        variants=[
            ('call_function','K2Node_CallFunction',dict(function_path='/Script/Engine.KismetSystemLibrary.PrintString')),
            ('set_variable','K2Node_VariableSet',dict(name='BusCount')),
            ('custom_event','K2Node_CustomEvent',dict(name='AcceptanceEvent')),
            ('event','K2Node_Event',dict(name='ReceiveDestroyed')),
            ('macro','K2Node_MacroInstance',dict(macro_path='/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:ForEachLoop')),
            ('return','K2Node_FunctionResult',dict(graph='AcceptanceFunction')),
            ('get_local','K2Node_VariableGet',dict(graph='AcceptanceFunction',name='LocalCount')),
            ('set_local','K2Node_VariableSet',dict(graph='AcceptanceFunction',name='LocalCount')),
            ('dispatcher_event','K2Node_CustomEvent',dict(name='OnAcceptance')),
            ('component_event','K2Node_ComponentBoundEvent',dict(component='AcceptanceMesh',name='OnComponentBeginOverlap'))]
        for index,(kind,expected,options) in enumerate(variants):
            node=add(kind,x=3000,y=index*300,**options)
            check('Node kind '+kind,node['class']==expected)
        compiled=call('compile_blueprint',dict(path=fixture))
        check('Final Blueprint compiles',compiled['status']=='BS_UP_TO_DATE' and not compiled['problems'])
        check('User reference info preserved',call('blueprint_info',dict(path='/ArkMCPTesting/BP_McpTest1'))==reference)
    except Exception as exc:
        failure=repr(exc)
    finally:
        if created:
            try:
                code=f"import unreal,json\nprint(json.dumps({{'deleted':unreal.EditorAssetLibrary.delete_asset({fixture!r})}}))"
                check('Fixture cleanup',call('run_python',dict(code=code))['deleted'])
            except Exception as exc:
                failure=(failure or '')+'; cleanup: '+repr(exc)
        try:
            check('Editor remains connected',call('editor_status')['connected_to']=='ShooterGame')
        except Exception as exc:
            failure=(failure or '')+'; status: '+repr(exc)
        process.stdin.close()
        process.wait(timeout=30)
        report=dict(fixture=fixture,passed=sum(x['passed'] for x in results),checks=len(results),failure=failure,results=results)
        (directory/'assertions.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    return 1 if failure else 0


if __name__=='__main__':
    sys.exit(main())
