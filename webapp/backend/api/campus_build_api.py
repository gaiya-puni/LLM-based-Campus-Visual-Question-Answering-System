"""校园资产发现与生成端点（提交、查询、审核、预览、发布）。"""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import paths  # noqa: F401
from flask import Blueprint, jsonify, request
from paths import BASE as _BASE
from runtime import facade
from flask_app import rate_limit
from api.review_guard import review_denied, review_token_ok
from scene_heatmaps import SCENE_IDS
# CampusProfileResolver / WebSearchClient 会被测试整体替换
# （`patch.object(server, "CampusProfileResolver", ...)`），所以必须作为模块属性存在。
from tools.campus_generator.discovery import CampusProfileResolver, DiscoveryOrchestrator  # noqa: F401
from tools.campus_generator.harvest import (AmapPoiHarvester, HarvestCache, JsonHarvester,
                                            PublicWebHarvester)
from tools.campus_generator.jobs import JobCapacityError, JobNotReadyError
from tools.campus_generator.publish import build_publish_plan, publish as _publish_campus_build
from tools.campus_generator.review import apply_review, load_review
from tools.campus_generator.web_search import WebSearchClient  # noqa: F401

bp = Blueprint('campus_build_api', __name__)

def _campus_build_profile_path(value):
    """Accept only an existing JSON profile under the repository tree."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('profile is required')
    path = Path(value).resolve()
    repo_root = Path(_BASE).resolve().parent.parent
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError('profile must be inside the project') from exc
    if path.suffix.lower() != '.json' or not path.is_file():
        raise ValueError('profile must be an existing JSON file')
    return path


def _campus_build_harvesters(payload, profile_path):
    """Build allow-listed harvesters from request data without arbitrary paths."""
    if not isinstance(payload, dict):
        raise ValueError('harvest must be an object')
    cache = HarvestCache(facade._CAMPUS_BUILD_ROOT / 'cache')
    harvesters = []
    candidates_path = payload.get('candidates')
    if candidates_path:
        harvesters.append(JsonHarvester(_campus_build_profile_path(candidates_path)))
    keywords = payload.get('amapKeywords') or []
    if keywords:
        if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
            raise ValueError('amapKeywords must be a list of strings')
        if not os.getenv('AMAP_WEB_SERVICE_KEY', '').strip():
            raise ValueError('高德 POI 采集未配置 AMAP_WEB_SERVICE_KEY，请在项目根目录 .env 配置后重启后端')
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
        harvesters.append(AmapPoiHarvester(profile, keywords, cache=cache))
    urls = payload.get('webUrls') or []
    if urls:
        if not isinstance(urls, list) or not all(isinstance(item, str) for item in urls):
            raise ValueError('webUrls must be a list of strings')
        harvesters.append(PublicWebHarvester(urls, cache=cache))
    if not harvesters:
        raise ValueError('at least one harvest source is required')
    return harvesters


@bp.route('/api/campus/build', methods=['POST'])
@rate_limit(6)
def submit_campus_build():
    """Queue a candidate build; output remains pending review in userdata."""
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    try:
        profile_path = _campus_build_profile_path(data.get('profile'))
        harvesters = _campus_build_harvesters(data.get('harvest'), profile_path)
        existing_config = data.get('baseConfig')
        if existing_config:
            existing_config = _campus_build_profile_path(existing_config)
        job_id = facade._CAMPUS_BUILD_MANAGER.submit(profile_path, harvesters, existing_config)
    except JobCapacityError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 503
    except (TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    return jsonify({'success': True, 'jobId': job_id, 'status': 'queued'}), 202


@bp.route('/api/campus/build/<job_id>', methods=['GET'])
@rate_limit(120)
def get_campus_build(job_id):
    if len(job_id) != 32 or not all(char in '0123456789abcdef' for char in job_id):
        return jsonify({'success': False, 'message': 'job id is invalid'}), 400
    record = facade._CAMPUS_BUILD_MANAGER.get(job_id)
    if record is None:
        return jsonify({'success': False, 'message': 'job not found'}), 404
    record.pop('profile', None)
    if record.get('output'):
        record['output'] = job_id
    return jsonify({'success': True, **record})


def _campus_job_output(job_id):
    if len(job_id) != 32 or not all(char in '0123456789abcdef' for char in job_id):
        raise ValueError('job id is invalid')
    output = facade._CAMPUS_BUILD_MANAGER.output_path(job_id)
    if output is None or not output.is_dir():
        raise LookupError('job not found')
    return output


@contextmanager
def _campus_job_operation(job_id, name):
    """Lease one stable bundle; fallback keeps lightweight test doubles usable."""
    operation = getattr(facade._CAMPUS_BUILD_MANAGER, 'operation', None)
    if operation is None:
        yield _campus_job_output(job_id)
        return
    with operation(job_id, name) as output:
        yield output


@bp.route('/api/campus/build/<job_id>/review', methods=['GET'])
@rate_limit(60)
def campus_build_review(job_id):
    try:
        with _campus_job_operation(job_id, 'review-read') as output:
            return jsonify({'success': True, 'jobId': job_id, **load_review(output)})
    except JobNotReadyError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except LookupError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404


@bp.route('/api/campus/build/<job_id>/review', methods=['POST'])
@rate_limit(30)
def update_campus_build_review(job_id):
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    try:
        with _campus_job_operation(job_id, 'review-write') as output:
            result = apply_review(output, data.get('decisions'))
            invalidate_preview = getattr(facade._CAMPUS_BUILD_MANAGER, 'invalidate_preview', None)
            if invalidate_preview is not None:
                invalidate_preview(job_id)
            return jsonify({'success': True, 'jobId': job_id, **result})
    except JobNotReadyError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    except (TypeError, ValueError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except LookupError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404


@bp.route('/api/campus/build/<job_id>/preview', methods=['POST'])
@rate_limit(10)
def submit_campus_build_preview(job_id):
    if not review_token_ok():
        return review_denied()
    try:
        facade._CAMPUS_BUILD_MANAGER.submit_preview(job_id)
        return jsonify({'success': True, 'jobId': job_id, 'previewStatus': 'queued'}), 202
    except JobCapacityError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 503
    except JobNotReadyError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except LookupError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404


@bp.route('/api/campus/build/<job_id>/publish-plan', methods=['GET'])
@rate_limit(30)
def get_campus_build_publish_plan(job_id):
    """Return a diff without changing formal POI assets."""
    try:
        with _campus_job_operation(job_id, 'publish-plan') as output:
            return jsonify({'success': True, 'jobId': job_id,
                            'plan': build_publish_plan(output)})
    except JobNotReadyError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except LookupError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404


@bp.route('/api/campus/build/<job_id>/publish', methods=['POST'])
@rate_limit(10)
def publish_campus_build(job_id):
    """Publish only after the caller explicitly confirms the displayed diff."""
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    try:
        with _campus_job_operation(job_id, 'publish') as output:
            result = _publish_campus_build(
                output,
                confirm=data.get('confirm') is True,
                expected_after_hash=data.get('expectedAfterHash'),
            )
            return jsonify({'success': True, 'jobId': job_id, **result})
    except JobNotReadyError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    except (TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except LookupError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404


@bp.route('/api/campus/build/<job_id>/preview/<scene>', methods=['GET'])
@rate_limit(120)
def get_campus_build_preview(job_id, scene):
    if scene not in SCENE_IDS:
        return jsonify({'success': False, 'message': 'scene is invalid'}), 400
    try:
        with _campus_job_operation(job_id, 'preview-read') as output:
            assert_preview_ready = getattr(
                facade._CAMPUS_BUILD_MANAGER, 'assert_preview_ready', None,
            )
            if assert_preview_ready is not None:
                assert_preview_ready(job_id)
            profile = json.loads((output / 'campus_profile.json').read_text(encoding='utf-8'))
            slug = str((profile.get('campus') or {}).get('slug') or '')
            season = request.args.get('season', '').strip().lower()
            if season and season not in {'spring', 'summer', 'autumn', 'winter'}:
                return jsonify({'success': False, 'message': 'season is invalid'}), 400
            suffix = f'_{season}' if season and scene in {'flower_viewing', 'photo'} else ''
            cache_file = output / 'preview_heatmap_cache' / f'{slug}_{scene}{suffix}.json'
            if not cache_file.is_file():
                return jsonify({'success': False, 'message': 'preview is not ready for this scene'}), 404
            payload = json.loads(cache_file.read_text(encoding='utf-8'))
            return jsonify({'success': True, 'jobId': job_id, 'preview': payload})
    except JobNotReadyError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except (LookupError, OSError, json.JSONDecodeError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404


@bp.route('/api/campus/discover', methods=['POST'])
@rate_limit(6)
def submit_campus_discovery():
    """Resolve a natural-language campus request and queue multi-source harvest."""
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    capacity_reservation = None
    try:
        query = data.get('query')
        include_amap = data.get('includeAmap', True)
        include_web = data.get('includeWeb', True)
        # API callers must opt in because each search can consume provider
        # quota.  The campus-build UI sends this flag explicitly.
        auto_search = data.get('autoSearch', False)
        if not all(isinstance(value, bool) for value in
                   (include_amap, include_web, auto_search)):
            raise ValueError('includeAmap、includeWeb 和 autoSearch 必须是布尔值')
        if include_amap and not os.getenv('AMAP_WEB_SERVICE_KEY', '').strip():
            raise ValueError('高德 POI 采集未配置 AMAP_WEB_SERVICE_KEY，请在项目根目录 .env 配置后重启后端；也可以关闭高德采集并提供公开网页 URL')
        cache = HarvestCache(facade._CAMPUS_BUILD_ROOT / 'cache')
        search_provider = os.getenv('WEB_SEARCH_PROVIDER', 'tavily').strip().lower()
        search_client = None
        search_unavailable = None
        if include_web and auto_search:
            key_names = {
                'tavily': 'TAVILY_API_KEY',
                'brave': 'BRAVE_SEARCH_API_KEY',
            }
            if search_provider not in key_names:
                raise ValueError(
                    f'WEB_SEARCH_PROVIDER={search_provider!r} 不受支持，请使用 tavily 或 brave'
                )
            key_name = key_names[search_provider]
            search_api_key = os.getenv(key_name, '').strip()
            if not search_api_key or search_api_key.startswith('replace-with-'):
                search_unavailable = (
                    f'自动搜索未启用：请在项目根目录 .env 配置 {key_name} 后重启后端；'
                    '本次仍会运行已登记数据、手工网址和已启用的高德来源'
                )
            else:
                search_client = facade.WebSearchClient(
                    search_provider,
                    search_api_key,
                    cache,
                    max_results=int(os.getenv('WEB_SEARCH_MAX_RESULTS', '10')),
                    per_domain_limit=int(os.getenv('WEB_SEARCH_PER_DOMAIN_LIMIT', '2')),
                    max_queries=int(os.getenv('WEB_SEARCH_MAX_QUERIES', '4')),
                    timeout=float(os.getenv('WEB_SEARCH_TIMEOUT', '8')),
                )
        orchestrator = DiscoveryOrchestrator(
            facade.CampusProfileResolver(api_key=os.getenv('AMAP_WEB_SERVICE_KEY')),
            cache=cache,
            web_search=search_client,
            web_search_unavailable=search_unavailable,
            web_search_provider=search_provider,
        )
        # Reserve before calling a metered search API.  This keeps a full
        # local queue from consuming provider credits for a job it cannot run.
        capacity_reservation = facade._CAMPUS_BUILD_MANAGER.reserve_capacity()
        plan = orchestrator.plan(
            query,
            keywords=data.get('keywords'),
            web_urls=data.get('webUrls'),
            include_amap=include_amap,
            include_web=include_web,
            auto_search=auto_search,
        )
        harvesters = orchestrator.harvesters(
            plan,
            include_amap=include_amap,
            include_web=include_web,
        )
        existing_config = data.get('baseConfig')
        if existing_config:
            existing_config = _campus_build_profile_path(existing_config)
        job_id = facade._CAMPUS_BUILD_MANAGER.submit_profile(
            plan.profile, harvesters, existing_config,
            metadata={'query': plan.query, 'theme': plan.theme,
                      'targetCategory': plan.targetCategory,
                      'keywords': plan.keywords, 'discoveryWarnings': plan.warnings,
                      'discovery': plan.profile.get('discovery', {}),
                      'webSearch': plan.webSearch},
            reservation=capacity_reservation,
        )
        capacity_reservation = None
    except JobCapacityError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 503
    except (TypeError, ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    finally:
        if capacity_reservation is not None:
            facade._CAMPUS_BUILD_MANAGER.release_capacity(capacity_reservation)
    return jsonify({
        'success': True,
        'jobId': job_id,
        'status': 'queued',
        'profile': plan.profile,
        'theme': plan.theme,
        'keywords': plan.keywords,
        'sources': plan.sources,
        'warnings': plan.warnings,
        'webSearch': plan.webSearch,
    }), 202
