<template>
    <div class="auth-page">
        <!-- partial:index.partial.html -->
        <div class="section">
            <div class="container">
                <div class="row full-height justify-content-center">
                    <div class="col-12 text-center align-self-center py-5">
                        <div class="section pb-5 pt-5 pt-sm-2 text-center">
                            <h6 class="mb-0 pb-3"><span>登录 </span><span>注册</span></h6>
                            <input class="checkbox" type="checkbox" id="reg-log" name="reg-log" />
                            <label for="reg-log"></label>
                            <div class="card-3d-wrap mx-auto">
                                <div class="card-3d-wrapper">
                                    <div class="card-front">
                                        <div class="center-wrap">
                                            <div class="section text-center">
                                                <h4 class="mb-4 pb-3">登录</h4>
                                                <div class="form-group">
                                                    <input type="text" name="logemail" class="form-style" placeholder="用戶名"
                                                        id="logname" autocomplete="off" />
                                                    <User class="input-icon" />
                                                </div>
                                                <div class="form-group mt-2">
                                                    <input type="password" name="logpass" class="form-style"
                                                        placeholder="密码" id="logpass" autocomplete="off" />
                                                    <Lock class="input-icon" />
                                                </div>
                                                <div id="warning"></div>
                                                <el-button @click="submit">提交</el-button>
                                                <p class="mb-0 mt-4 text-center">
                                                    <el-link class="link" @click="changePassword">忘记密码</el-link>
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="card-back">
                                        <div class="center-wrap">
                                            <div class="section text-center">
                                                <h4 class="mb-4 pb-3">注册</h4>
                                                <el-form ref="ruleFormRef" :model="user" status-icon :rules="rules"
                                                    label-width="100px" class="demo-ruleForm">
                                                    <div class="form-group">
                                                        <input type="text" name="regname" class="form-style"
                                                            placeholder="用戶名" id="regname" autocomplete="off"
                                                            v-model="user.name" />
                                                        <User class="input-icon" />
                                                    </div>
                                                    <div class="form-group mt-2">
                                                        <input type="email" name="regemail" class="form-style"
                                                            placeholder="电邮" id="regemail" autocomplete="off"
                                                            v-model="user.email" />
                                                        <Message class="input-icon" />
                                                    </div>
                                                    <div class="form-group mt-2">
                                                        <input type="password" name="regpass" class="form-style"
                                                            placeholder="密码" id="regpass" autocomplete="off"
                                                            v-model="user.password" />
                                                        <Lock class="input-icon" />
                                                    </div>
                                                    <div id="register-warning"></div>
                                                    <el-button @click="onSubmit()" :plain="true">提交</el-button>
                                                </el-form>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import router from "@/router";
import { reactive, ref } from 'vue'
import { ElMessage, FormInstance } from 'element-plus'

const ruleFormRef = ref<FormInstance>()
// 这里存放数据
const user = reactive({
    email: '',
    name: '',
    password: '',
})

//校验
const validatePassword = (rule: any, value: any, callback: any) => {
    if (value === '') {
        callback(new Error('请输入密码'))
    } else {
        callback()
    }
}

const validatename = (rule: any, value: any, callback: any) => {
    if (value === '') {
        callback(new Error('请输入账号'))
    } else {
        callback()
    }
}

//暂时无验证码，需要的话随时加上
/*const validateVerification = (rule: any, value: any, callback: any) => {
    if (value === '') {
        callback(new Error('请输入验证码'))
    } else {
        callback()
    }
}*/
//校验
const rules = reactive({
    password: [{ validator: validatePassword, trigger: 'blur' }],
    name: [{ validator: validatename, trigger: 'blur' }],
})

const changePassword = () => {
    router.replace('/changePassword')
}

const submit = async () => {
    const username = (document.getElementById('logname') as HTMLInputElement)?.value
    const password = (document.getElementById('logpass') as HTMLInputElement)?.value

    if (!username || !password) {
        document.getElementById('warning')!.innerText = '请输入用户名和密码'
        return
    }

    try {
        const response = await fetch('/api/login_json', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        })
        const data = await response.json()

        if (data.success) {
            router.push('/index')
        } else {
            document.getElementById('warning')!.innerText = data.message || '登录失败'
        }
    } catch (error) {
        document.getElementById('warning')!.innerText = '后端服务未连接，请先启动 Flask 后端'
    }
}

const onSubmit = async () => {
    if (!user.name || !user.password) {
        document.getElementById('register-warning')!.innerText = '请输入用户名和密码'
        return
    }

    try {
        const response = await fetch('/api/register_json', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: user.name, password: user.password })
        })
        const data = await response.json()

        if (data.success) {
            ElMessage({ message: '注册成功，请登录', type: 'success', duration: 2000 })
            // 翻转到登录面
            ;(document.getElementById('reg-log') as HTMLInputElement).checked = false
        } else {
            document.getElementById('register-warning')!.innerText = data.message || '注册失败'
        }
    } catch (error) {
        document.getElementById('register-warning')!.innerText = '后端服务未连接，暂时无法注册'
    }
}
</script>

<style lang="scss" scoped>
@use "./auth-common.scss";

h6 span {
    padding: 0 22px;
    text-transform: uppercase;
    font-weight: 700;
    font-size: 20px;
    letter-spacing: 0;
}

h6 {
    margin: 0;
}

.checkbox:checked+label,
.checkbox:not(:checked)+label {
    position: relative;
    display: block;
    text-align: center;
    width: 86px;
    height: 24px;
    border-radius: 999px;
    padding: 0;
    margin: 22px auto 38px;
    cursor: pointer;
    background-color: #ffeba7;
}

.checkbox:checked+label:before,
.checkbox:not(:checked)+label:before {
    position: absolute;
    display: block;
    width: 46px;
    height: 46px;
    border-radius: 50%;
    color: #ffeba7;
    background-color: #102770;
    content: "";
    z-index: 20;
    top: -11px;
    left: -8px;
    line-height: 46px;
    text-align: center;
    transition: all 0.5s ease;
}

.checkbox:checked+label:before {
    transform: translateX(56px) rotate(-270deg);
}

.center-wrap {
    top: 46%;
}

.auth-page :deep(.el-link) {
    font-size: 17px;
    font-weight: 600;
}

#register-warning {
    min-height: 24px;
    margin-top: 14px;
    margin-bottom: 14px;
    text-align: start;
    color: red;
}
</style>
