<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">系统管理 <span class="sub">平台配置、用户与域控对接</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="平台概况" name="overview">
        <div class="stat-grid">
          <div class="stat-card">
            <div class="label"><el-icon><Monitor /></el-icon>平台版本</div>
            <div class="value" style="font-size: 20px">{{ health.version || '-' }}</div>
            <div class="foot">{{ health.app || 'NetOps' }}</div>
          </div>
          <div class="stat-card">
            <div class="label"><el-icon><Timer /></el-icon>调度器</div>
            <div class="value" style="font-size: 20px" :class="health.scheduler_running ? 'text-ok' : 'text-muted'">
              {{ health.scheduler_running ? '运行中' : '未运行' }}
            </div>
            <div class="foot">{{ (health.jobs || []).length }} 个定时任务</div>
          </div>
          <div class="stat-card">
            <div class="label"><el-icon><Connection /></el-icon>Syslog 接收</div>
            <div class="value" style="font-size: 20px">UDP {{ health.syslog_udp_port || '-' }}</div>
            <div class="foot">TCP 同端口</div>
          </div>
          <div class="stat-card">
            <div class="label"><el-icon><Connection /></el-icon>采集通道</div>
            <div class="value" style="font-size: 20px">{{ channelSummary }}</div>
            <div class="foot">SNMP / Telemetry / NetFlow</div>
          </div>
        </div>

        <div class="table-card" style="margin-top: 12px">
          <div class="card-title">采集通道状态</div>
          <el-table :data="channelRows" size="small" stripe>
            <el-table-column prop="name" label="通道" width="140" />
            <el-table-column prop="desc" label="说明" min-width="320" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="140" align="center">
              <template #default="{ row }">
                <el-tag :type="row.running ? 'success' : 'danger'" size="small" effect="plain">
                  {{ row.running ? '运行中' : '未运行' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="detail" label="端口 / 详情" min-width="200" class-name="mono" />
          </el-table>
        </div>

        <div class="table-card" style="margin-top: 12px">
          <div class="card-title">
            <span>定时任务配置</span>
            <span class="text-muted" style="font-size: 12px">
              可直接修改调度策略、启停任务或立即执行，保存后立即生效
            </span>
          </div>
          <el-table v-loading="loadingJobs" :data="jobs" size="small" stripe>
            <el-table-column prop="name" label="任务名称" width="150" show-overflow-tooltip />
            <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.category }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="调度策略" width="270">
              <template #default="{ row }">
                <div class="job-schedule">
                  <el-input
                    v-model="row.cron"
                    size="small"
                    placeholder="cron 表达式（留空用间隔）"
                    style="width: 160px"
                    :disabled="!userStore.isAdmin"
                    @change="(v) => saveJob(row, 'cron', v)"
                  />
                  <el-input-number
                    v-model="row.interval_seconds"
                    size="small"
                    :min="0"
                    :step="10"
                    controls-position="right"
                    style="width: 100px"
                    :disabled="!userStore.isAdmin"
                    @change="(v) => saveJob(row, 'interval_seconds', v)"
                  />
                </div>
                <div class="job-hint">{{ row.schedule_text }}</div>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="70" align="center">
              <template #default="{ row }">
                <el-switch
                  :model-value="row.enabled"
                  :loading="togglingJob === row.job_id"
                  :disabled="!userStore.isAdmin"
                  @change="(v) => toggleJob(row, v)"
                />
              </template>
            </el-table-column>
            <el-table-column label="下次执行" width="155">
              <template #default="{ row }">
                <span class="mono">{{ row.next_run_at ? row.next_run_at.replace('T', ' ').slice(0, 19) : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="上次执行" width="155">
              <template #default="{ row }">
                <span class="mono">{{ row.last_run_at ? row.last_run_at.replace('T', ' ').slice(0, 19) : '未执行' }}</span>
                <el-tag
                  v-if="row.last_status"
                  size="small"
                  effect="plain"
                  :type="row.last_status === 'success' ? 'success' : 'danger'"
                  style="margin-left: 4px"
                >
                  {{ row.last_status === 'success' ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  size="small"
                  :loading="runningJob === row.job_id"
                  @click="runJob(row)"
                >
                  立即执行
                </el-button>
                <el-button link type="primary" size="small" @click="resetJob(row)">恢复默认</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="用户管理" name="users">
        <div class="table-card">
          <div class="filter-bar">
            <el-button v-if="userStore.isAdmin" type="primary" :icon="Plus" @click="openUser()">
              新增用户
            </el-button>
            <span class="text-muted">角色权限：管理员 &gt; 操作员 &gt; 只读</span>
          </div>
          <el-table v-loading="loading" :data="users" size="small" stripe>
            <el-table-column prop="username" label="用户名" width="140" />
            <el-table-column prop="display_name" label="姓名" width="130" />
            <el-table-column prop="email" label="邮箱" min-width="170" show-overflow-tooltip />
            <el-table-column prop="department" label="部门" width="130" show-overflow-tooltip />
            <el-table-column label="来源" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.source === 'ldap' ? 'warning' : 'info'">
                  {{ row.source === 'ldap' ? '域账号' : '本地' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="角色" min-width="150" align="center">
              <template #default="{ row }">
                <el-tag
                  v-for="r in rowRoles(row)"
                  :key="r"
                  size="small"
                  :type="roleType(r)"
                  style="margin: 1px"
                >
                  {{ roleText(r) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.is_active ? 'success' : 'danger'">
                  {{ row.is_active ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最后登录" width="150">
              <template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template>
            </el-table-column>
            <el-table-column v-if="userStore.isAdmin" label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openUser(row)">编辑</el-button>
                <el-button link type="danger" size="small" @click="removeUser(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="域控对接" name="ldap">
        <div class="gap-row">
          <div class="table-card" style="flex: 1; min-width: 420px">
            <div class="card-title">
              <span>Active Directory / LDAP</span>
              <el-switch
                v-model="ldapForm['ldap.enabled']"
                :disabled="!userStore.isAdmin"
                active-text="启用域控登录"
                @change="saveSettings"
              />
            </div>

            <el-alert
              v-if="!ldapForm['ldap.enabled']"
              type="info"
              :closable="false"
              show-icon
              style="margin-bottom: 14px"
            >
              <template #title>域控登录当前未启用</template>
              开启上方开关并填写服务器地址、Base DN 与绑定账号后保存，即可使用域账号登录平台。
              配置保存在数据库中，保存后立即生效，无需重启服务。
            </el-alert>

            <el-form label-width="130px" size="small" :disabled="!userStore.isAdmin">
              <el-form-item label="服务器地址">
                <el-input v-model="ldapForm['ldap.server']" placeholder="如：ldap://dc.corp.local" />
              </el-form-item>
              <el-form-item label="端口 / 加密">
                <el-input-number v-model="ldapForm['ldap.port']" :min="1" :max="65535" style="width: 120px" />
                <el-checkbox v-model="ldapForm['ldap.use_ssl']" style="margin-left: 16px">SSL</el-checkbox>
                <el-checkbox v-model="ldapForm['ldap.start_tls']" style="margin-left: 12px">StartTLS</el-checkbox>
              </el-form-item>
              <el-form-item label="Base DN">
                <el-input v-model="ldapForm['ldap.base_dn']" placeholder="如：DC=corp,DC=local" />
              </el-form-item>
              <el-form-item label="绑定账号">
                <el-input v-model="ldapForm['ldap.bind_dn']" placeholder="服务账号 DN（可留空匿名绑定）" />
              </el-form-item>
              <el-form-item label="绑定密码">
                <el-input v-model="ldapForm['ldap.bind_password']" type="password" show-password
                  placeholder="留空表示不修改" />
              </el-form-item>
              <el-form-item label="用户过滤器">
                <el-input v-model="ldapForm['ldap.user_filter']" placeholder="(sAMAccountName={username})" />
              </el-form-item>
              <el-form-item label="用户 DN 模板">
                <el-input v-model="ldapForm['ldap.user_dn_template']" placeholder="CN={username},OU=Users,DC=corp,DC=local（可留空）" />
              </el-form-item>
              <el-form-item label="域名 / 默认角色">
                <el-input v-model="ldapForm['ldap.domain']" placeholder="如：CORP" style="width: 180px" />
                <el-select v-model="ldapForm['ldap.default_role']" style="width: 130px; margin-left: 10px">
                  <el-option label="只读" value="viewer" />
                  <el-option label="操作员" value="operator" />
                  <el-option label="管理员" value="admin" />
                </el-select>
              </el-form-item>
              <el-form-item label="AD 组角色映射">
                <el-input
                  v-model="ldapForm['ldap.group_role_map']"
                  type="textarea"
                  :rows="3"
                  placeholder='{"CN=NetOps-Admins,OU=Groups,DC=corp,DC=local":"admin"}'
                />
              </el-form-item>
            </el-form>

            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 6px">
              <el-button v-if="userStore.isAdmin" type="primary" :icon="Check" :loading="saving" @click="saveSettings">
                保存配置
              </el-button>
              <el-button :icon="Connection" @click="testLdap">测试连接</el-button>
              <el-button :icon="Refresh" @click="syncLdap">同步域用户</el-button>
              <el-button :icon="View" @click="viewDomainUsers">查看域用户</el-button>
            </div>

            <el-descriptions :column="1" size="small" border style="margin-top: 14px">
              <el-descriptions-item label="连接状态">
                <el-tag size="small" :type="ldapStatus.ok ? 'success' : 'danger'">
                  {{ ldapStatus.ok ? '连接正常' : '未连接' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="说明">{{ ldapStatus.message || '-' }}</el-descriptions-item>
            </el-descriptions>
          </div>

          <div class="table-card" style="flex: 1; min-width: 380px">
            <div class="card-title">登录审计</div>
            <el-table :data="loginLogs" size="small" stripe max-height="420">
              <el-table-column prop="username" label="用户名" width="120" show-overflow-tooltip />
              <el-table-column label="结果" width="70" align="center">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="row.success ? 'success' : 'danger'">
                    {{ row.success ? '成功' : '失败' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="source" label="来源" width="80" align="center">
                <template #default="{ row }">{{ row.source }}</template>
              </el-table-column>
              <el-table-column prop="ip" label="IP" width="120" class-name="mono" />
              <el-table-column label="时间" width="150">
                <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="采集服务" name="collector">
        <div class="table-card">
          <div class="card-title">
            <span>采集通道开关</span>
            <span class="text-muted" style="font-size: 12px">开关立即生效，无需重启服务</span>
          </div>
          <el-table :data="collectorRows" size="small" stripe>
            <el-table-column prop="name" label="采集通道" width="170" />
            <el-table-column prop="desc" label="说明" min-width="320" show-overflow-tooltip />
            <el-table-column prop="detail" label="详情" width="220" class-name="mono" />
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.running ? 'success' : 'info'">
                  {{ row.running ? '运行中' : '已关闭' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="userStore.isAdmin" label="开关" width="120" align="center">
              <template #default="{ row }">
                <el-switch
                  :model-value="collectorSwitch[row.key]"
                  :loading="toggling === row.key"
                  @change="(v) => toggleCollector(row.key, v)"
                />
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="table-card" style="margin-top: 12px">
          <div class="card-title">采集参数</div>
          <el-form label-width="150px" size="small" :disabled="!userStore.isAdmin">
            <el-form-item label="gNMI 监听端口">
              <el-input-number v-model="settingsForm['collector.gnmi_port']" :min="1024" :max="65535" />
              <span class="text-muted" style="margin-left: 10px; font-size: 12px">
                修改后需重启 gNMI Collector 生效（关闭再开启）
              </span>
            </el-form-item>
            <el-form-item label="NetFlow 监听端口">
              <el-input v-model="settingsForm['collector.netflow_ports']" placeholder="2055,4739,9995" />
              <span class="text-muted" style="margin-left: 10px; font-size: 12px">
                逗号分隔，修改后需重启 NetFlow 采集器生效
              </span>
            </el-form-item>
          </el-form>
          <el-button v-if="userStore.isAdmin" type="primary" :icon="Check" :loading="saving" @click="saveSettings">
            保存参数
          </el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane label="AI 助手" name="ai">
        <div class="table-card">
          <div class="card-title">
            <span>大模型接入</span>
            <el-switch
              v-model="aiForm.enabled"
              :disabled="!userStore.isAdmin"
              active-text="启用 AI 助手"
              @change="saveAiConfig"
            />
          </div>
          <el-form label-width="140px" size="small" :disabled="!userStore.isAdmin">
            <el-form-item label="服务商">
              <el-select v-model="aiForm.provider" style="width: 280px" @change="onProviderChange">
                <el-option v-for="p in providers" :key="p.value" :label="p.label" :value="p.value" />
              </el-select>
              <span class="text-muted" style="margin-left: 10px; font-size: 12px">{{ providerNote }}</span>
            </el-form-item>
            <el-form-item label="API 地址">
              <el-input v-model="aiForm.base_url" placeholder="留空则使用服务商默认地址" />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input v-model="aiForm.api_key" type="password" show-password placeholder="留空表示不修改" />
            </el-form-item>
            <el-form-item label="模型">
              <el-input v-model="aiForm.model" placeholder="留空则使用服务商默认模型" />
            </el-form-item>
            <el-form-item label="参数">
              <el-input-number v-model="aiForm.temperature" :min="0" :max="2" :step="0.1" :precision="1" />
              <span class="text-muted" style="margin: 0 14px">温度</span>
              <el-input-number v-model="aiForm.max_tokens" :min="256" :max="32000" :step="256" />
              <span class="text-muted" style="margin-left: 8px">最大 tokens</span>
              <el-input-number v-model="aiForm.timeout" :min="10" :max="600" :step="10" style="margin-left: 14px" />
              <span class="text-muted" style="margin-left: 8px">超时(秒)</span>
            </el-form-item>
            <el-form-item label="注入平台数据">
              <el-switch v-model="aiForm.context_enabled" />
              <span class="text-muted" style="margin-left: 10px; font-size: 12px">
                自动附带设备、指标、告警、日志、拓扑等上下文
              </span>
            </el-form-item>
            <el-form-item label="系统提示词">
              <el-input v-model="aiForm.system_prompt" type="textarea" :rows="4" placeholder="留空使用默认提示词" />
            </el-form-item>
          </el-form>
          <div style="display: flex; gap: 10px; margin-top: 6px">
            <el-button v-if="userStore.isAdmin" type="primary" :icon="Check" :loading="saving" @click="saveAiConfig">
              保存配置
            </el-button>
            <el-button :icon="Connection" @click="testAi">测试连接</el-button>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="外观" name="appearance">
        <div class="table-card">
          <div class="card-title">界面外观</div>
          <el-form label-width="140px" size="small">
            <el-form-item label="主题模式">
              <el-radio-group :model-value="themeStore.mode" @change="(v) => themeStore.setMode(v)">
                <el-radio-button v-for="opt in themeOptions" :key="opt.value" :value="opt.value">
                  <el-icon><component :is="opt.icon" /></el-icon>
                  {{ opt.label }}
                </el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="当前生效">
              <el-tag size="small" effect="plain">
                {{ themeStore.resolved === 'dark' ? '深色' : '浅色' }}
                {{ themeStore.mode === 'system' ? '（跟随系统）' : '' }}
              </el-tag>
            </el-form-item>
            <el-form-item label="默认主题">
              <el-select :model-value="settingsForm['ui.default_theme']" style="width: 180px"
                @change="(v) => { settingsForm['ui.default_theme'] = v; saveSettings() }">
                <el-option label="浅色" value="light" />
                <el-option label="深色" value="dark" />
                <el-option label="跟随系统" value="system" />
              </el-select>
              <span class="text-muted" style="margin-left: 10px; font-size: 12px">
                新用户登录时的默认主题
              </span>
            </el-form-item>
          </el-form>
        </div>
      </el-tab-pane>

      <el-tab-pane label="平台说明" name="about">
        <div class="table-card">
          <div class="card-title">功能清单</div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="多厂商采集">
              MikroTik RouterOS、Cisco IOS/NXOS、华为 VRP、新华三 H3C Comware（SNMP v1/v2c/v3 + SSH 兜底）
            </el-descriptions-item>
            <el-descriptions-item label="实时接口速率">
              基于 IF-MIB 64 位计数器差分计算，支持 10G 及以上端口，自动识别协商速率
            </el-descriptions-item>
            <el-descriptions-item label="资源监控">CPU 使用率、内存使用率、设备温度、SNMP 响应时延、运行时长</el-descriptions-item>
            <el-descriptions-item label="告警">阈值规则 + 静默期 + 自动恢复，支持 Webhook / 企业微信 / 钉钉 / 邮件通知</el-descriptions-item>
            <el-descriptions-item label="日志">内置 Syslog UDP/TCP 接收服务（RFC3164 / RFC5424），支持 SSH 主动拉取</el-descriptions-item>
            <el-descriptions-item label="资产管理">设备台账、资产编号、序列号、保修到期提醒、采购与合同信息</el-descriptions-item>
            <el-descriptions-item label="门店管理">按门店 / 网点组织设备，统计各门店在线率与资产分布</el-descriptions-item>
            <el-descriptions-item label="流量分析">接口 / 设备 / 门店三个维度的流量 TopN 排行与趋势分析</el-descriptions-item>
            <el-descriptions-item label="配置备份">SSH 抓取 running-config，自动归档、版本对比、定时备份</el-descriptions-item>
            <el-descriptions-item label="基线巡检">可定制检查项模板，自动判定并生成 HTML / Excel 巡检报告</el-descriptions-item>
            <el-descriptions-item label="域控对接">LDAP / Active Directory 认证、用户同步、AD 组映射角色</el-descriptions-item>
          </el-descriptions>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="userDialog" :title="userForm.id ? '编辑用户' : '新增用户'" width="520px">
      <el-form ref="userFormRef" :model="userForm" :rules="userRules" label-width="90px" size="small">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="userForm.username" :disabled="!!userForm.id" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="userForm.password" type="password" show-password
            :placeholder="userForm.id ? '留空则不修改' : ''" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="userForm.display_name" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="userForm.email" />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input v-model="userForm.phone" />
        </el-form-item>
        <el-form-item label="部门">
          <el-input v-model="userForm.department" />
        </el-form-item>
        <el-form-item label="角色" prop="roles">
          <el-select v-model="userForm.roles" multiple style="width: 100%" placeholder="可多选，如同时具备操作员与审批员">
            <el-option
              v-for="opt in roleOptions"
              :key="opt.key"
              :label="opt.label"
              :value="opt.key"
            >
              <div class="role-option">
                <span>{{ opt.label }}</span>
                <span class="role-option-desc">{{ opt.desc }}</span>
              </div>
            </el-option>
          </el-select>
          <div class="form-tip">管理员已含全部权限；审批员可审批配置变更，不含设备操作权</div>
        </el-form-item>
        <el-form-item v-if="userForm.id" label="状态">
          <el-switch v-model="userForm.is_active" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="domainDialog" title="域用户列表" width="820px">
      <el-table :data="domainUsers" size="small" stripe max-height="480">
        <el-table-column prop="username" label="域账号" width="140" />
        <el-table-column prop="display_name" label="姓名" width="140" show-overflow-tooltip />
        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />
        <el-table-column prop="department" label="部门" width="130" show-overflow-tooltip />
        <el-table-column label="映射角色" min-width="140" align="center">
          <template #default="{ row }">
            <el-tag
              v-for="r in rowRoles(row)"
              :key="r"
              size="small"
              :type="roleType(r)"
              style="margin: 1px"
            >
              {{ roleText(r) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="AD 组" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="g in (row.groups || []).slice(0, 3)" :key="g" size="small" effect="plain" style="margin: 2px">
              {{ g.split(',')[0] }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Connection, Plus, Refresh, View } from '@element-plus/icons-vue'
import { aiApi, authApi, schedulerApi, settingsApi, systemApi } from '../api'
import { useUserStore } from '../store/user'
import { useThemeStore, THEME_OPTIONS } from '../store/theme'

const userStore = useUserStore()
const themeStore = useThemeStore()
const tab = ref('overview')
const health = ref({})
const users = ref([])
const loginLogs = ref([])
const ldapStatus = ref({})
const domainUsers = ref([])
const loading = ref(false)
const saving = ref(false)
const toggling = ref(null)

// 运行时设置
const settingsForm = reactive({})
const ldapForm = reactive({})
const aiForm = reactive({})
const providers = ref([])
const collectorRows = ref([])
const collectorSwitch = reactive({})

const userDialog = ref(false)
const userFormRef = ref()
const userForm = reactive({})
const domainDialog = ref(false)
// 角色选项（从后端拉取，含说明文案）
const roleOptions = ref([
  { key: 'admin', label: '管理员', desc: '全部权限，含审批权' },
  { key: 'operator', label: '操作员', desc: '设备增删改、执行采集、备份、巡检、提交变更申请' },
  { key: 'approver', label: '审批员', desc: '审批配置变更申请，不含设备操作权' },
  { key: 'viewer', label: '只读', desc: '仅查看，不可修改任何配置' },
])

const themeOptions = THEME_OPTIONS

const providerNote = computed(
  () => providers.value.find((p) => p.value === aiForm.provider)?.note || '',
)

const userRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    {
      validator: (rule, value, cb) => {
        if (!userForm.id && !value) cb(new Error('请输入密码'))
        else if (value && value.length < 4) cb(new Error('密码至少 4 位'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
  roles: [
    {
      validator: (rule, value, cb) => {
        if (!value || !value.length) cb(new Error('请至少选择一个角色'))
        else cb()
      },
      trigger: 'change',
    },
  ],
}

const jobDesc = {
  collect_all: '周期性采集全部设备的接口速率与 CPU/内存指标',
  check_offline: '检测设备离线状态并触发离线告警',
  recover_offline: '设备恢复后自动关闭离线告警',
  collect_logs: '通过 SSH 定期拉取交换机日志',
  daily_backup: '每日自动执行设备配置备份',
  hourly_inspection_dispatch: '调度计划巡检任务',
  cleanup: '清理过期指标与日志数据',
}

const jobRows = computed(() =>
  (health.value.jobs || []).map((id) => ({ id, desc: jobDesc[id] || '-' })),
)

// ---------------- 定时任务配置 ----------------
const jobs = ref([])
const loadingJobs = ref(false)
const togglingJob = ref(null)
const runningJob = ref(null)

async function loadJobs() {
  loadingJobs.value = true
  try {
    const res = await schedulerApi.jobs()
    jobs.value = res.items || []
  } catch (e) {
    jobs.value = []
  } finally {
    loadingJobs.value = false
  }
}

async function saveJob(row, field, value) {
  try {
    await schedulerApi.updateJob(row.job_id, {
      cron: row.cron || '',
      interval_seconds: row.interval_seconds || 0,
      enabled: row.enabled,
    })
    ElMessage.success(`${row.name} 调度已更新`)
    await loadJobs()
  } catch (e) {
    /* 拦截器已提示，恢复原值 */
    await loadJobs()
  }
}

async function toggleJob(row, enabled) {
  togglingJob.value = row.job_id
  try {
    await schedulerApi.toggleJob(row.job_id)
    ElMessage.success(`${row.name} 已${enabled ? '启用' : '停用'}`)
    await loadJobs()
  } catch (e) {
    /* 忽略 */
  } finally {
    togglingJob.value = null
  }
}

async function runJob(row) {
  runningJob.value = row.job_id
  try {
    const res = await schedulerApi.runJob(row.job_id)
    if (res.ok) ElMessage.success(`${row.name} 执行完成`)
    else ElMessage.warning(`${row.name} 执行失败：${res.error || '未知错误'}`)
    await loadJobs()
  } catch (e) {
    /* 忽略 */
  } finally {
    runningJob.value = null
  }
}

async function resetJob(row) {
  try {
    await schedulerApi.resetJob(row.job_id)
    ElMessage.success(`${row.name} 已恢复默认调度`)
    await loadJobs()
  } catch (e) {
    /* 忽略 */
  }
}

const channelRows = computed(() => collectorRows.value)

const channelSummary = computed(
  () => `${channelRows.value.filter((c) => c.running).length} / ${channelRows.value.length}`,
)

function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}
function roleText(r) {
  return { admin: '管理员', operator: '操作员', approver: '审批员', viewer: '只读' }[r] || r
}
function roleType(r) {
  return { admin: 'danger', operator: 'warning', approver: 'success', viewer: 'info' }[r] || 'info'
}
// 取行的角色集合（历史数据只有单角色时回退）
function rowRoles(row) {
  if (Array.isArray(row?.roles) && row.roles.length) return row.roles
  return row?.role ? [row.role] : ['viewer']
}

async function loadHealth() {
  try {
    health.value = await systemApi.health()
  } catch (e) {
    health.value = {}
  }
}

async function loadUsers() {
  loading.value = true
  try {
    const res = await authApi.listUsers()
    users.value = res.items || []
  } catch (e) {
    users.value = []
  } finally {
    loading.value = false
  }
}

// 拉取后端角色清单（含说明文案），失败时保留内置兜底
async function loadRoles() {
  try {
    const res = await authApi.listRoles()
    if (Array.isArray(res.items) && res.items.length) {
      roleOptions.value = res.items
    }
  } catch (e) {
    /* 忽略，使用内置默认值 */
  }
}

async function loadLoginLogs() {
  try {
    const res = await authApi.loginLogs(100)
    loginLogs.value = res.items || []
  } catch (e) {
    loginLogs.value = []
  }
}

async function reload() {
  await Promise.all([loadHealth(), loadUsers(), loadLoginLogs(), loadSettings(), loadJobs(), loadRoles()])
}

async function loadSettings() {
  try {
    const res = await settingsApi.get()
    Object.keys(settingsForm).forEach((k) => delete settingsForm[k])
    Object.assign(settingsForm, res.items || {})
    // LDAP 表单单独维护
    Object.keys(ldapForm).forEach((k) => delete ldapForm[k])
    Object.assign(ldapForm, res.items || {})
    // 采集开关
    Object.keys(collectorSwitch).forEach((k) => delete collectorSwitch[k])
    const cols = res.collector || {}
    Object.assign(collectorSwitch, {
      snmp: !!cols.snmp?.running,
      telemetry: !!cols.telemetry?.running,
      netflow: !!cols.netflow?.running,
      syslog: !!cols.syslog?.running,
    })
    collectorRows.value = [
      { key: 'snmp', name: 'SNMP 轮询', ...(cols.snmp || {}) },
      { key: 'telemetry', name: 'Telemetry（gNMI）', ...(cols.telemetry || {}) },
      { key: 'netflow', name: 'NetFlow / IPFIX', ...(cols.netflow || {}) },
      { key: 'syslog', name: 'Syslog 接收', ...(cols.syslog || {}) },
    ]
  } catch (e) {
    /* 忽略 */
  }
  try {
    const res = await aiApi.config()
    Object.keys(aiForm).forEach((k) => delete aiForm[k])
    Object.assign(aiForm, {
      enabled: !!res.enabled,
      provider: res.provider || 'openai',
      base_url: res.base_url || '',
      api_key: '',
      model: res.model || '',
      temperature: res.temperature ?? 0.3,
      max_tokens: res.max_tokens ?? 2048,
      timeout: res.timeout ?? 60,
      context_enabled: res.context_enabled !== false,
      system_prompt: res.system_prompt || '',
    })
  } catch (e) {
    /* 忽略 */
  }
  try {
    const res = await aiApi.providers()
    providers.value = res.items || []
  } catch (e) {
    providers.value = []
  }
}

async function saveSettings() {
  saving.value = true
  try {
    const payload = { ...ldapForm, ...settingsForm }
    delete payload['ai.enabled']
    delete payload['ai.provider']
    delete payload['ai.base_url']
    delete payload['ai.api_key']
    delete payload['ai.model']
    delete payload['ai.temperature']
    delete payload['ai.max_tokens']
    delete payload['ai.timeout']
    delete payload['ai.context_enabled']
    delete payload['ai.system_prompt']
    const res = await settingsApi.update(payload)
    ElMessage.success(`已保存${res.changed?.length ? `（${res.changed.length} 项）` : ''}`)
    await loadSettings()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function saveAiConfig() {
  saving.value = true
  try {
    const payload = {
      enabled: aiForm.enabled,
      provider: aiForm.provider,
      base_url: aiForm.base_url,
      model: aiForm.model,
      temperature: aiForm.temperature,
      max_tokens: aiForm.max_tokens,
      timeout: aiForm.timeout,
      context_enabled: aiForm.context_enabled,
      system_prompt: aiForm.system_prompt,
    }
    if (aiForm.api_key && aiForm.api_key !== '********') {
      payload.api_key = aiForm.api_key
    }
    await aiApi.updateConfig(payload)
    ElMessage.success('AI 配置已保存')
    await loadSettings()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

function onProviderChange(provider) {
  const p = providers.value.find((x) => x.value === provider)
  if (p) {
    if (!aiForm.base_url) aiForm.base_url = p.base_url
    if (!aiForm.model) aiForm.model = p.model
  }
}

async function testAi() {
  const res = await aiApi.test()
  if (res.ok) ElMessage.success(res.message)
  else ElMessage.warning(res.message)
}

async function toggleCollector(target, enabled) {
  toggling.value = target
  try {
    await settingsApi.toggleCollector(target, enabled)
    ElMessage.success(`${target} 已${enabled ? '启用' : '关闭'}`)
    await loadSettings()
  } catch (e) {
    /* 忽略 */
  } finally {
    toggling.value = null
  }
}

function onTabChange(name) {
  if (name === 'ldap') {
    testLdap()
    loadLoginLogs()
  }
}

function openUser(row) {
  Object.keys(userForm).forEach((k) => delete userForm[k])
  Object.assign(
    userForm,
    {
      id: null, username: '', password: '', display_name: '', email: '', phone: '',
      roles: ['viewer'], department: '', is_active: true,
    },
    row ? { ...row, password: '', roles: rowRoles(row) } : {},
  )
  userDialog.value = true
}

async function saveUser() {
  const ok = await userFormRef.value?.validate().catch(() => false)
  if (!ok) return
  saving.value = true
  try {
    if (userForm.id) {
      const payload = {
        display_name: userForm.display_name,
        email: userForm.email,
        phone: userForm.phone,
        roles: userForm.roles,
        department: userForm.department,
        is_active: userForm.is_active,
      }
      if (userForm.password) payload.password = userForm.password
      await authApi.updateUser(userForm.id, payload)
    } else {
      await authApi.createUser({
        username: userForm.username,
        password: userForm.password,
        display_name: userForm.display_name,
        email: userForm.email,
        phone: userForm.phone,
        roles: userForm.roles,
        department: userForm.department,
      })
    }
    ElMessage.success('保存成功')
    userDialog.value = false
    loadUsers()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function removeUser(row) {
  try {
    await ElMessageBox.confirm(`确定删除用户「${row.username}」吗？`, '提示', { type: 'warning' })
    await authApi.deleteUser(row.id)
    ElMessage.success('已删除')
    loadUsers()
  } catch (e) {
    /* 取消 */
  }
}

async function testLdap() {
  try {
    ldapStatus.value = await authApi.ldapTest()
  } catch (e) {
    ldapStatus.value = { ok: false, message: e?.response?.data?.detail || '测试失败' }
  }
}

async function syncLdap() {
  const res = await authApi.ldapSync()
  if (res.ok) {
    ElMessage.success(res.message)
    loadUsers()
  } else {
    ElMessage.warning(res.message)
  }
}

async function viewDomainUsers() {
  const res = await authApi.ldapUsers()
  if (res.ok) {
    domainUsers.value = res.users || []
    domainDialog.value = true
  } else {
    ElMessage.warning(res.message)
  }
}

onMounted(async () => {
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>
