"""重建锁定版本的 Backlog.md，应用归档 ID、中文界面、Agent 交接、调研交接、待验收状态与流程体验补丁。"""
from pathlib import Path
import hashlib,json,os,subprocess,tarfile


def main():
    root=Path(__file__).resolve().parents[1]
    spec=json.loads((root/'patches/backlog-source.json').read_text())
    source=root/'vendor/backlog-source'
    archive=root/'work/build/backlog-v1.53.0.tar.gz'
    archive.parent.mkdir(parents=True,exist_ok=True)
    if not archive.exists():
        with archive.open('wb') as out:
            subprocess.run(['gh','api','repos/'+spec['upstream']+'/tarball/'+spec['commit']],stdout=out,check=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=spec['archive_sha256']:
        raise ValueError('上游源码归档校验不一致；拒绝构建，不能自动更新校验值')
    if not source.exists():
        source.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            prefix=tar.getmembers()[0].name.split('/')[0]+'/'
            for member in tar.getmembers():
                if not member.name.startswith(prefix):continue
                member.name=member.name[len(prefix):]
                if member.name:tar.extract(member,source,filter='data')
    target=source/'src/core/backlog.ts'
    if 'Project Inbox: archived issues' not in target.read_text():
        subprocess.run(['/usr/bin/patch','-p1','-i',str(root/spec['patch'])],cwd=source,check=True)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=spec['patched_file_sha256']:
        raise ValueError('补丁后的源码与已验证版本不一致，拒绝构建')
    def web_matches(key):
        for relative,hashes in spec['web_files_sha256'].items():
            path=source/relative
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            # 后续补丁会继续修改部分界面文件，以最后一层补丁的结果为准。
            finals=[spec.get(name,{}).get(relative,{}).get('after') for name in ['handoff_files_sha256','review_files_sha256','research_files_sha256','ux_files_sha256']]
            if actual!=hashes[key] and not (key=='after' and actual in [f for f in finals if f]):return False
        return True
    if not web_matches('after'):
        if not web_matches('before'):
            raise ValueError('界面源码与固定补丁的输入不一致，拒绝覆盖')
        subprocess.run(['/usr/bin/patch','-p1','-i',str(root/spec['web_patch'])],cwd=source,check=True)
    if not web_matches('after'):
        raise ValueError('中文界面补丁校验失败')
    def handoff_matches(key):
        for relative,hashes in spec['handoff_files_sha256'].items():
            path=source/relative
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            finals=[spec.get(name,{}).get(relative,{}).get('after') for name in ['research_files_sha256','ux_files_sha256']]
            if actual!=hashes[key] and not (key=='after' and actual in [f for f in finals if f]):return False
        return True
    if not handoff_matches('after'):
        if not handoff_matches('before'):
            raise ValueError('Agent 交接源码与固定补丁的输入不一致，拒绝覆盖')
        subprocess.run(['/usr/bin/patch','-p1','-i',str(root/spec['handoff_patch'])],cwd=source,check=True)
    if not handoff_matches('after'):
        raise ValueError('Agent 交接补丁校验失败')
    def review_matches(key):
        for relative,hashes in spec['review_files_sha256'].items():
            path=source/relative
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            # 流程体验补丁会继续修改这些文件，以最后一层补丁的结果为准。
            if actual!=hashes[key] and not (key=='after' and actual==spec.get('ux_files_sha256',{}).get(relative,{}).get('after')):return False
        return True
    if not review_matches('after'):
        if not review_matches('before'):
            raise ValueError('待验收状态源码与固定补丁的输入不一致，拒绝覆盖')
        subprocess.run(['/usr/bin/patch','-p1','-i',str(root/spec['review_patch'])],cwd=source,check=True)
    if not review_matches('after'):
        raise ValueError('待验收状态补丁校验失败')
    def research_matches(key):
        for relative,hashes in spec['research_files_sha256'].items():
            path=source/relative
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if actual!=hashes[key] and not (key=='after' and actual==spec.get('ux_files_sha256',{}).get(relative,{}).get('after')):return False
        return True
    if not research_matches('after'):
        if not research_matches('before'):
            raise ValueError('调研交接源码与固定补丁的输入不一致，拒绝覆盖')
        subprocess.run(['/usr/bin/patch','-p1','-i',str(root/spec['research_patch'])],cwd=source,check=True)
    if not research_matches('after'):
        raise ValueError('调研交接补丁校验失败')
    def ux_matches(key):
        for relative,hashes in spec['ux_files_sha256'].items():
            path=source/relative
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if actual!=hashes[key]:return False
        return True
    if not ux_matches('after'):
        if not ux_matches('before'):
            raise ValueError('流程体验源码与固定补丁的输入不一致，拒绝覆盖')
        subprocess.run(['/usr/bin/patch','-p1','-i',str(root/spec['ux_patch'])],cwd=source,check=True)
    if not ux_matches('after'):
        raise ValueError('流程体验补丁校验失败')
    bun=root/'node_modules/@oven/bun-darwin-aarch64/bin/bun'
    subprocess.run([str(bun),'install','--frozen-lockfile','--ignore-scripts'],cwd=source,check=True)
    output=root/'vendor/backlog-runtime/backlog'
    output.parent.mkdir(parents=True,exist_ok=True)
    staged=output.with_suffix('.building')
    env=dict(os.environ,BACKLOG_BUILD_OUTFILE=str(staged),BACKLOG_BUILD_VERSION=spec['runtime_version'])
    subprocess.run([str(bun),'scripts/build.ts'],cwd=source,env=env,check=True)
    subprocess.run([str(staged),'--version'],check=True)
    import shutil
    shutil.copy2(source/'LICENSE',output.parent/'LICENSE')
    os.replace(staged,output)
    print('Runtime:',output)


if __name__=='__main__':main()
