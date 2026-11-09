// SPDX-License-Identifier: GPL-2.0
/* Disposable-emulator test module, not a production driver. */
#include <linux/i2c.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/preempt.h>

static int bus = 22;
module_param(bus, int, 0400);
MODULE_PARM_DESC(bus, "Disposable emulator's direct BCM2835 adapter number");

static bool confirm_disposable;
module_param(confirm_disposable, bool, 0400);
MODULE_PARM_DESC(confirm_disposable, "Explicitly permit tests on a disposable emulator");

static int atomic_transfer(struct i2c_adapter *adapter,
			   struct i2c_msg *messages, int count)
{
	unsigned long flags;
	int ret;

	i2c_lock_bus(adapter, I2C_LOCK_ROOT_ADAPTER);
	preempt_disable();
	local_irq_save(flags);
	ret = adapter->algo->xfer_atomic(adapter, messages, count);
	local_irq_restore(flags);
	preempt_enable();
	i2c_unlock_bus(adapter, I2C_LOCK_ROOT_ADAPTER);
	return ret;
}

static int __init uconsole_atomic_test_init(void)
{
	struct i2c_adapter *adapter;
	u8 pointer = 3, value = 0;
	struct i2c_msg messages[] = {
		{ .addr = 0x34, .len = 1, .buf = &pointer },
		{ .addr = 0x34, .flags = I2C_M_RD, .len = 1, .buf = &value },
	};
	struct i2c_msg missing = {
		.addr = 0x7f, .flags = I2C_M_RD, .len = 1, .buf = &value,
	};
	int ret;

	if (!confirm_disposable)
		return -EPERM;
	adapter = i2c_get_adapter(bus);
	if (!adapter)
		return -ENODEV;
	if (!adapter->algo->xfer_atomic) {
		ret = -EOPNOTSUPP;
		goto out;
	}

	/* Combined register-pointer write/read exercises repeated start. */
	ret = atomic_transfer(adapter, messages, ARRAY_SIZE(messages));
	if (ret != 2 || value != 6) {
		pr_err("uconsole atomic combined read: ret=%d value=%u\n", ret, value);
		ret = -EIO;
		goto out;
	}
	ret = atomic_transfer(adapter, &missing, 1);
	if (ret != -EREMOTEIO) {
		pr_err("uconsole atomic NACK: ret=%d\n", ret);
		ret = -EIO;
		goto out;
	}
	value = 0;
	ret = atomic_transfer(adapter, messages, ARRAY_SIZE(messages));
	if (ret != 2 || value != 6) {
		pr_err("uconsole atomic recovery: ret=%d value=%u\n", ret, value);
		ret = -EIO;
		goto out;
	}
	value = 0;
	ret = i2c_transfer(adapter, messages, ARRAY_SIZE(messages));
	if (ret != 2 || value != 6) {
		pr_err("uconsole ordinary recovery: ret=%d value=%u\n", ret, value);
		ret = -EIO;
		goto out;
	}
	pr_info("UCONSOLE_ATOMIC_TEST_PASS combined-read nack atomic-recovery ordinary-recovery\n");
	ret = 0;
out:
	i2c_put_adapter(adapter);
	return ret;
}

static void __exit uconsole_atomic_test_exit(void)
{
}

module_init(uconsole_atomic_test_init);
module_exit(uconsole_atomic_test_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Disposable uConsole emulator atomic I2C acceptance test");
