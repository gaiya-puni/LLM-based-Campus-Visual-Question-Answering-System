<template>
    <div class="auth-page">
        <!-- partial:index.partial.html -->
        <div class="section">
            <div class="container">
                <div class="row full-height justify-content-center">
                    <div class="col-12 text-center align-self-center py-5">
                        <div class="section pb-5 pt-5 pt-sm-2 text-center">
                            <div class="card-3d-wrap mx-auto">
                                <div class="card-3d-wrapper">
                                    <div class="card-front">
                                        <div class="center-wrap">
                                            <div class="section text-center">
                                                <h4 class="mb-4 pb-3">修改密码</h4>
                                                <div class="form-group">
                                                    <input type="text" v-model="username" name="logemail" class="form-style" placeholder="用戶名"
                                                        id="logname" autocomplete="off" />
                                                    <User class="input-icon" />
                                                </div>
                                                <div class="form-group mt-2">
                                                    <input type="password" v-model="currentPassword" name="currentpass" class="form-style"
                                                        placeholder="当前密码" id="currentpass" autocomplete="current-password" />
                                                    <Lock class="input-icon" />
                                                </div>
                                                <div class="form-group mt-2">
                                                    <input type="password" v-model="newPassword" name="logpass" class="form-style"
                                                        placeholder="新密码（至少8位）" id="logpass" autocomplete="new-password" />
                                                    <Lock class="input-icon" />
                                                </div>
                                                <div id="warning">{{ warningMsg }}</div>
                                                <el-button @click="back">返回</el-button>

                                                <el-button @click="submitChange">提交</el-button>
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
import { ref } from "vue";
import axios from "axios";
import router from "../router";

const username = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const warningMsg = ref('')

const back = () => {
    router.replace('/')
}

const submitChange = async () => {
    if (!username.value || !currentPassword.value || !newPassword.value) {
        warningMsg.value = '用户名、当前密码和新密码不能为空'
        return
    }
    if (newPassword.value.length < 8) {
        warningMsg.value = '新密码至少需要8位'
        return
    }
    try {
        const res = await axios.post('/api/change_password', {
            username: username.value,
            currentPassword: currentPassword.value,
            newPassword: newPassword.value
        })
        if (res.data.success) {
            warningMsg.value = '密码修改成功，即将跳转登录页…'
            setTimeout(() => router.replace('/'), 1500)
        } else {
            warningMsg.value = res.data.message || '修改失败'
        }
    } catch (error: any) {
        warningMsg.value = error?.response?.data?.message || '密码修改失败，请检查当前密码或后端服务'
    }
}
</script>

<style lang="scss" scoped>
@use "./auth-common.scss";

.center-wrap {
    top: 50%;
}

#back {
    background-color: #FFACA7;
}
</style>
